"""Shared HTTP client, TTL cache with in-flight de-dupe, latency-ranked fallback chains with per-source health.

Every source carries an EWMA of its latency and failure rate, per request family ("klines", "tickers", ...).
`chain()` tries the cheapest (fastest x most reliable) source first; a background prober keeps the numbers fresh
for sources that would otherwise only be measured when the faster ones fail.
"""
import asyncio
import math
import time
from collections import defaultdict
from typing import Any

import httpx

UA_BROWSER = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0 Safari/537.36")
UA_BARE = "Mozilla/5.0"  # Yahoo 429s full Chrome UAs
UA_SEC = "Rook Market Analyser research admin@rookery.local"  # SEC requires a contact-style UA

_client: httpx.AsyncClient | None = None
ADAPTIVE = {"on": True}  # toggled from settings.data.adaptive_latency


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            headers={"User-Agent": UA_BROWSER, "Accept": "*/*"},
        )
    return _client


async def close():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class SourceError(Exception):
    pass


async def get_json(url, *, ua=None, headers=None, params=None, timeout=None):
    h = dict(headers or {})
    if ua:
        h["User-Agent"] = ua
    r = await client().get(url, headers=h, params=params, timeout=timeout)
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code} {url.split('?')[0]}")
    return r.json()


async def get_text(url, *, ua=None, headers=None, params=None, timeout=None):
    h = dict(headers or {})
    if ua:
        h["User-Agent"] = ua
    r = await client().get(url, headers=h, params=params, timeout=timeout)
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code} {url.split('?')[0]}")
    return r.text


async def post_json(url, body, *, timeout=None, headers=None):
    r = await client().post(url, json=body, timeout=timeout, headers=headers)
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code} {url.split('?')[0]}")
    return r.json()


# ------------------------------------------------------------------ health + latency
ALPHA = 0.3       # EWMA weight of the newest latency sample
FAIL_DECAY = 0.8  # failure EWMA: fail -> 0.8*f + 0.2 ; ok -> 0.8*f
FAIL_PENALTY = 4  # cost = latency * (1 + FAIL_PENALTY * fail_rate)


def family(chain_name):
    return (chain_name or "").split(":")[0]


class Health:
    def __init__(self):
        self.s = defaultdict(lambda: {"ok": 0, "fail": 0, "last_ok": None, "last_fail": None,
                                      "last_error": None, "latency_ms": None, "ewma_ms": None,
                                      "fail_rate": 0.0, "chain": None})
        self.lat: dict[tuple[str, str], dict] = {}

    def _lat(self, fam, src):
        return self.lat.setdefault((fam, src), {"ewma_ms": None, "fail_rate": 0.0, "n": 0})

    def ok(self, chain, src, ms):
        e = self.s[src]
        e["ok"] += 1
        e["last_ok"] = time.time()
        e["chain"] = chain
        e["fail_rate"] *= FAIL_DECAY
        if ms and ms > 0:  # 0 = "not timed" (e.g. bulk feed sweeps)
            e["latency_ms"] = round(ms)
            e["ewma_ms"] = ms if e["ewma_ms"] is None else ALPHA * ms + (1 - ALPHA) * e["ewma_ms"]
            lt = self._lat(family(chain), src)
            lt["ewma_ms"] = ms if lt["ewma_ms"] is None else ALPHA * ms + (1 - ALPHA) * lt["ewma_ms"]
            lt["fail_rate"] *= FAIL_DECAY
            lt["n"] += 1

    def fail(self, chain, src, err):
        e = self.s[src]
        e["fail"] += 1
        e["last_fail"] = time.time()
        e["last_error"] = str(err)[:200]
        e["chain"] = chain
        e["fail_rate"] = FAIL_DECAY * e["fail_rate"] + (1 - FAIL_DECAY)
        lt = self._lat(family(chain), src)
        lt["fail_rate"] = FAIL_DECAY * lt["fail_rate"] + (1 - FAIL_DECAY)

    def cost(self, src, fam=None):
        """Expected cost (ms, penalised by failures). Family-specific if measured, else the probe/global number."""
        for key in ((fam or "", src), ("ping", src)):
            lt = self.lat.get(key)
            if lt and lt["ewma_ms"] is not None:
                return lt["ewma_ms"] * (1 + FAIL_PENALTY * lt["fail_rate"])
        e = self.s.get(src)
        if e and e["ewma_ms"] is not None:
            return e["ewma_ms"] * (1 + FAIL_PENALTY * e["fail_rate"])
        return None

    def snapshot(self):
        now = time.time()
        out = []
        for src, e in sorted(self.s.items()):
            status = "unknown"
            if e["last_ok"] and (not e["last_fail"] or e["last_ok"] >= e["last_fail"]):
                status = "ok"
            elif e["last_fail"]:
                status = "down"
            out.append({"source": src, **{k: v for k, v in e.items() if k != "ewma_ms"},
                        "ewma_ms": round(e["ewma_ms"]) if e["ewma_ms"] is not None else None,
                        "status": status, "age_s": round(now - e["last_ok"]) if e["last_ok"] else None})
        return out


HEALTH = Health()


def order(names, fam=None):
    """Rank source names by measured cost (latency x reliability). Unmeasured sources are placed at the median
    measured cost, so they are neither favoured nor buried; ties keep the static priority order."""
    if not ADAPTIVE["on"] or len(names) < 2:
        return list(names)
    costs = {n: HEALTH.cost(n, fam) for n in names}
    known = sorted((c for c in costs.values() if c is not None))
    if not known:
        return list(names)
    fill = known[len(known) // 2]  # unknown -> median: neither favoured nor buried
    return hysteresis_rank(list(names), {n: (c if c is not None else fill) for n, c in costs.items()})


def hysteresis_rank(names, cost, tol=1.25):
    """Greedy ordering with real hysteresis: at each step take the highest-priority (earliest) name whose cost is
    within `tol` of the cheapest remaining one. A faster source only overtakes when it is >25% faster, so two
    near-equal sources never flap on noise (fixed log-buckets would still flip at bucket edges)."""
    rest, out = list(names), []
    while rest:
        best = min(cost[n] for n in rest)
        pick = next(n for n in rest if cost[n] <= best * tol)
        out.append(pick)
        rest.remove(pick)
    return out


# Sources that recently failed get skipped for a cooldown so the chain answers fast. Keyed by (chain, source): a source
# that can't serve klines:HYPE must not be skipped for klines:BTC, and a quote batch failing doesn't sideline it elsewhere.
_cooldown: dict[tuple[str, str], float] = {}


class NotApplicable(SourceError):
    """This source can't serve this request (wrong market, interval, symbol set). Not a failure: no health hit,
    no cooldown, the chain just moves on."""


async def chain(name, attempts, *, cooldown_s=60, adaptive=True):
    """attempts: list of (source_name, zero-arg async fn). Returns (data, source). Raises if all fail."""
    errors = []
    now = time.time()
    if adaptive:
        rank = {n: i for i, n in enumerate(order([a[0] for a in attempts], family(name)))}
        attempts = sorted(attempts, key=lambda a: rank[a[0]])
    ordered = [a for a in attempts if _cooldown.get((name, a[0]), 0) <= now] or attempts
    for src, fn in ordered:
        t0 = time.perf_counter()
        try:
            data = await fn()
            if data is None or (hasattr(data, "__len__") and len(data) == 0):
                raise SourceError("empty")
            HEALTH.ok(name, src, (time.perf_counter() - t0) * 1000)
            _cooldown.pop((name, src), None)
            return data, src
        except NotApplicable as e:
            errors.append(f"{src}: n/a ({e})")
        except Exception as e:  # noqa: BLE001
            HEALTH.fail(name, src, e)
            _cooldown[(name, src)] = time.time() + cooldown_s
            errors.append(f"{src}: {e}")
    raise SourceError(f"{name}: all sources failed -> " + " | ".join(errors))


# ------------------------------------------------------------------ latency prober
PROBES = {
    # name: (method, url, json body | None) -- tiny endpoints, measured on warm keep-alive connections
    "binance": ("GET", "https://api.binance.com/api/v3/time", None),
    "binance.vision": ("GET", "https://data-api.binance.vision/api/v3/time", None),
    "bybit": ("GET", "https://api.bybit.com/v5/market/time", None),
    "okx": ("GET", "https://www.okx.com/api/v5/public/time", None),
    "coinbase": ("GET", "https://api.exchange.coinbase.com/time", None),
    "coingecko": ("GET", "https://api.coingecko.com/api/v3/ping", None),
    "jupiter": ("GET", "https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112", None),
    "elfa": ("GET", "https://api.elfa.ai/v2/ping", None),
}
PROBE_HIST: dict[str, list] = defaultdict(list)  # name -> [(ts, ms|None)] last 60


async def probe_once(extra: dict | None = None):
    probes = {**PROBES, **(extra or {})}

    async def one(name, spec):
        method, url, body = spec
        t0 = time.perf_counter()
        try:
            r = await client().request(method, url, json=body, timeout=6)
            if r.status_code >= 400:
                raise SourceError(f"HTTP {r.status_code}")
            ms = (time.perf_counter() - t0) * 1000
            HEALTH.ok("ping", name, ms)
        except Exception as e:  # noqa: BLE001
            ms = None
            HEALTH.fail("ping", name, e)
        h = PROBE_HIST[name]
        h.append((time.time(), None if ms is None else round(ms, 1)))
        del h[:-60]
        return name, ms

    return dict(await asyncio.gather(*(one(n, s) for n, s in probes.items())))


def latency_report():
    out = []
    for name, h in PROBE_HIST.items():
        vals = [v for _, v in h if v is not None]
        vals_sorted = sorted(vals)
        out.append({"source": name, "last_ms": h[-1][1] if h else None,
                    "p50_ms": vals_sorted[len(vals_sorted) // 2] if vals else None,
                    "p90_ms": vals_sorted[int(len(vals_sorted) * 0.9)] if vals else None,
                    "fail_pct": round(100 * (1 - len(vals) / len(h)), 1) if h else None,
                    "cost": HEALTH.cost(name, "ping"), "samples": len(h)})
    out.sort(key=lambda r: (r["cost"] is None, r["cost"] or 0))
    return out


# ------------------------------------------------------------------ cache
_cache: dict[str, tuple[float, object]] = {}
_inflight: dict[str, asyncio.Future] = {}


async def cached(key, ttl, fn, *, stale_ok=True) -> Any:
    """TTL cache. On refresh failure, serves stale data (flagged by caller via cache_age)."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if key in _inflight:
        return await _inflight[key]
    fut = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        val = await fn()
        _cache[key] = (time.time(), val)
        fut.set_result(val)
        return val
    except Exception as e:  # noqa: BLE001
        if stale_ok and hit:
            fut.set_result(hit[1])
            return hit[1]
        fut.set_exception(e)
        fut.exception()  # mark retrieved
        raise
    finally:
        _inflight.pop(key, None)


def cache_age(key):
    hit = _cache.get(key)
    return None if not hit else time.time() - hit[0]


def peek(key):
    hit = _cache.get(key)
    return hit[1] if hit else None


def put(key, val):
    _cache[key] = (time.time(), val)
