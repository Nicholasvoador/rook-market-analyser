"""Crypto market data with fallback chains. Every public function returns plain JSON-able data."""
import asyncio
import html
import json
import re
import time
from collections import deque

import websockets

from .. import net
from ..config import secret
from ..net import UA_BROWSER, cached, chain, get_json, get_text, post_json

BINANCE = "https://api.binance.com"
BINANCE_VISION = "https://data-api.binance.vision"
BFUT = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com/v5/market"
OKX = "https://www.okx.com/api/v5/market"
COINBASE = "https://api.exchange.coinbase.com"
CG = "https://api.coingecko.com/api/v3"
PAPRIKA = "https://api.coinpaprika.com/v1"

INTERVALS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
_BYBIT_IV = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
_OKX_IV = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}
_CB_IV = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
IV_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _cg_headers():
    k = secret("COINGECKO_API_KEY")
    return {"x-cg-demo-api-key": k} if k else None


# ------------------------------------------------------------------ venue listing / resolve
async def _timed(fam, src, coro):
    """Await a network call and record its real latency / failure (cache hits never reach here)."""
    t0 = time.perf_counter()
    try:
        out = await coro
    except Exception as e:  # noqa: BLE001
        net.HEALTH.fail(fam, src, e)
        raise
    net.HEALTH.ok(fam, src, (time.perf_counter() - t0) * 1000)
    return out


async def _all_tickers_binance():
    async def fetch():
        d = await _timed("tickers", "binance", get_json(f"{BINANCE}/api/v3/ticker/24hr", params={"type": "FULL"}))
        return {t["symbol"]: t for t in d}
    return await cached("tick:binance", 12, fetch)


async def _all_tickers_bybit():
    async def fetch():
        d = await _timed("tickers", "bybit", get_json(f"{BYBIT}/tickers", params={"category": "spot"}))
        return {t["symbol"]: t for t in d["result"]["list"]}
    return await cached("tick:bybit", 12, fetch)


async def _all_tickers_okx():
    async def fetch():
        d = await _timed("tickers", "okx", get_json(f"{OKX}/tickers", params={"instType": "SPOT"}))
        return {t["instId"]: t for t in d["data"]}
    return await cached("tick:okx", 12, fetch)


def _norm_binance(base, t):
    return {"sym": base, "venue": "binance", "pair": t["symbol"], "last": _f(t["lastPrice"]),
            "open24": _f(t["openPrice"]), "high24": _f(t["highPrice"]), "low24": _f(t["lowPrice"]),
            "chg24": _f(t["priceChangePercent"]), "volq": _f(t["quoteVolume"])}


def _norm_bybit(base, t):
    last, prev = _f(t["lastPrice"]), _f(t.get("prevPrice24h"))
    return {"sym": base, "venue": "bybit", "pair": t["symbol"], "last": last, "open24": prev,
            "high24": _f(t.get("highPrice24h")), "low24": _f(t.get("lowPrice24h")),
            "chg24": (_f(t.get("price24hPcnt"), 0) or 0) * 100, "volq": _f(t.get("turnover24h"))}


def _norm_okx(base, t):
    last, op = _f(t["last"]), _f(t.get("open24h"))
    return {"sym": base, "venue": "okx", "pair": t["instId"], "last": last, "open24": op,
            "high24": _f(t.get("high24h")), "low24": _f(t.get("low24h")),
            "chg24": ((last / op - 1) * 100) if last and op else None, "volq": _f(t.get("volCcy24h"))}


_VENUES = {
    "binance": (_all_tickers_binance, lambda b: f"{b}USDT", _norm_binance),
    "bybit": (_all_tickers_bybit, lambda b: f"{b}USDT", _norm_bybit),
    "okx": (_all_tickers_okx, lambda b: f"{b}-USDT", _norm_okx),
}
WS_ENDPOINTS = {
    "binance": {"url": "wss://stream.binance.com:9443/stream", "alt": "wss://data-stream.binance.vision/stream"},
    "bybit": {"url": "wss://stream.bybit.com/v5/public/spot"},
    "okx": {"url": "wss://ws.okx.com:8443/ws/v5/public", "business": "wss://ws.okx.com:8443/ws/v5/business"},
}


def venue_order():
    """Spot venues ranked by measured latency x reliability (falls back to static priority)."""
    return net.order(list(_VENUES), "tickers")


async def tickers(bases):
    """24h tickers for base symbols, each from the fastest venue (by measured latency) that lists it."""
    bases = [b.upper() for b in bases]
    out, missing = {}, list(bases)
    for venue in venue_order():
        loader, pair_of, norm = _VENUES[venue]
        if not missing:
            break
        try:
            allt = await loader()
        except Exception:  # noqa: BLE001
            continue
        nxt = []
        for b in missing:
            t = allt.get(pair_of(b))
            if t and _f(t.get("lastPrice") or t.get("last")):
                out[b] = norm(b, t)
            else:
                nxt.append(b)
        missing = nxt
    return [out[b] for b in bases if b in out]


async def listings(base):
    """Every spot venue that lists BASE/USDT, ranked by latency: [{venue, pair}]."""
    base = base.upper()
    out = []
    for venue in venue_order():
        loader, pair_of, _ = _VENUES[venue]
        try:
            allt = await loader()
        except Exception:  # noqa: BLE001
            continue
        t = allt.get(pair_of(base))
        if t and _f(t.get("lastPrice") or t.get("last")):
            out.append({"venue": venue, "pair": pair_of(base)})
    return out


async def resolve(base):
    """Which venue/pair the browser should stream for this base. Includes every listing venue (latency-ranked)
    so the browser can race them and keep the fastest."""
    base = base.upper()
    t = await tickers([base])
    if not t:
        return None
    v = t[0]
    venues = [{**x, "ws": WS_ENDPOINTS[x["venue"]], "rest_ms": net.HEALTH.cost(x["venue"], "ping")}
              for x in await listings(base)]
    return {"sym": base, "venue": v["venue"], "pair": v["pair"], "ws": WS_ENDPOINTS[v["venue"]], "last": v["last"],
            "venues": venues}


# ------------------------------------------------------------------ klines
def _row(t, o, h, l, c, v):
    return {"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}


async def _kl_binance(pair, iv, limit, end_ms=None, host=BINANCE):
    p = {"symbol": pair, "interval": iv, "limit": min(limit, 1000)}
    if end_ms:
        p["endTime"] = end_ms
    d = await get_json(f"{host}/api/v3/klines", params=p)
    # k[7] quote volume, k[8] trades, k[10] taker-buy quote volume (aggressive buyers)
    return [{**_row(k[0] // 1000, k[1], k[2], k[3], k[4], k[7]), "tb": float(k[10]), "n": int(k[8])} for k in d]


async def _kl_bybit(pair, iv, limit, end_ms=None):
    p = {"category": "spot", "symbol": pair, "interval": _BYBIT_IV[iv], "limit": min(limit, 1000)}
    if end_ms:
        p["end"] = end_ms
    d = await get_json(f"{BYBIT}/kline", params=p)
    return [_row(int(k[0]) // 1000, k[1], k[2], k[3], k[4], k[6]) for k in reversed(d["result"]["list"])]


async def _kl_okx(pair, iv, limit):
    d = await get_json(f"{OKX}/candles", params={"instId": pair, "bar": _OKX_IV[iv], "limit": min(limit, 300)})
    return [_row(int(k[0]) // 1000, k[1], k[2], k[3], k[4], k[7]) for k in reversed(d["data"])]


async def _kl_coinbase(base, iv, limit):
    g = _CB_IV.get(iv)
    if not g:
        raise net.NotApplicable("interval unsupported")
    d = await get_json(f"{COINBASE}/products/{base}-USD/candles", params={"granularity": g})
    rows = [_row(k[0], k[3], k[2], k[1], k[4], k[5] * k[4]) for k in reversed(d)]
    return rows[-limit:]


def _listed_on(base):
    """Venues whose cached ticker list contains BASE/USDT; None if no ticker list is cached yet."""
    found, any_cached = set(), False
    for venue, (_, pair_of, _) in _VENUES.items():
        allt = net.peek(f"tick:{venue}")
        if allt is None:
            continue
        any_cached = True
        if pair_of(base) in allt:
            found.add(venue)
    return found if any_cached else None


async def klines(base, interval="1h", limit=500):
    base = base.upper()
    if interval not in IV_SEC:
        raise ValueError("bad interval")

    async def fetch():
        attempts = [
            ("binance", lambda: _kl_binance(f"{base}USDT", interval, limit)),
            ("binance.vision", lambda: _kl_binance(f"{base}USDT", interval, limit, host=BINANCE_VISION)),
            ("bybit", lambda: _kl_bybit(f"{base}USDT", interval, limit)),
            ("okx", lambda: _kl_okx(f"{base}-USDT", interval, limit)),
            ("coinbase", lambda: _kl_coinbase(base, interval, limit)),
        ]
        # skip venues known not to list the pair (saves a failing round-trip per request)
        listed = _listed_on(base)
        if listed is not None:
            keep = listed | ({"binance.vision"} if "binance" in listed else set()) | {"coinbase"}
            attempts = [a for a in attempts if a[0] in keep] or attempts
        rows, src = await chain(f"klines:{base}", attempts, cooldown_s=5)
        return {"sym": base, "interval": interval, "source": src, "rows": rows}

    ttl = 2 if IV_SEC[interval] <= 300 else 15
    return await cached(f"kl:{base}:{interval}:{limit}", ttl, fetch)


async def klines_history(base, interval="1h", bars=8760):
    """Paged deep history (Binance -> Bybit), oldest first."""
    base = base.upper()
    out, end = [], None
    use = "binance"
    while len(out) < bars:
        try:
            if use == "binance":
                page = await _kl_binance(f"{base}USDT", interval, 1000, end)
            else:
                page = await _kl_bybit(f"{base}USDT", interval, 1000, end)
        except Exception:  # noqa: BLE001
            if use == "binance" and not out:
                use = "bybit"
                continue
            break
        if not page:
            break
        out = page + out
        end = page[0]["t"] * 1000 - 1
        if len(page) < 900:
            break
        await asyncio.sleep(0.15)
    seen, dedup = set(), []
    for r in out:
        if r["t"] not in seen:
            seen.add(r["t"])
            dedup.append(r)
    return dedup[-bars:]


async def price_at(base, ts):
    """Close of the 30m bar ending at ts (i.e. the price at ts), used to grade predictions."""
    start_ms = int((ts - 1800) * 1000)
    attempts = [
        ("binance", lambda: get_json(f"{BINANCE}/api/v3/klines",
                                     params={"symbol": f"{base}USDT", "interval": "30m", "startTime": start_ms, "limit": 1})),
        ("bybit", lambda: get_json(f"{BYBIT}/kline", params={"category": "spot", "symbol": f"{base}USDT",
                                                              "interval": "30", "start": start_ms, "limit": 1})),
    ]
    d, src = await chain(f"price_at:{base}", attempts, cooldown_s=5)
    if src == "binance":
        k = d[0]
        return float(k[4]) if int(k[0]) == start_ms else None
    lst = d["result"]["list"]
    return float(lst[-1][4]) if lst else None


# ------------------------------------------------------------------ markets / global / trending
def _cg_market(c):
    return {"id": c["id"], "sym": c["symbol"].upper(), "name": c["name"], "image": c.get("image"),
            "price": c.get("current_price"), "mcap": c.get("market_cap"), "fdv": c.get("fully_diluted_valuation"),
            "vol": c.get("total_volume"), "rank": c.get("market_cap_rank"),
            "chg1h": c.get("price_change_percentage_1h_in_currency"),
            "chg24h": c.get("price_change_percentage_24h_in_currency"),
            "chg7d": c.get("price_change_percentage_7d_in_currency"),
            "chg30d": c.get("price_change_percentage_30d_in_currency"),
            "ath": c.get("ath"), "ath_chg": c.get("ath_change_percentage"), "circ": c.get("circulating_supply"),
            "total": c.get("total_supply"), "max": c.get("max_supply"),
            "spark": ((c.get("sparkline_in_7d") or {}).get("price") or [])[::4]}


def _pap_market(c):
    u = c["quotes"]["USD"]
    return {"id": c["id"], "sym": c["symbol"].upper(), "name": c["name"], "image": None, "price": u["price"],
            "mcap": u["market_cap"], "fdv": None, "vol": u["volume_24h"], "rank": c["rank"],
            "chg1h": u.get("percent_change_1h"), "chg24h": u.get("percent_change_24h"),
            "chg7d": u.get("percent_change_7d"), "chg30d": u.get("percent_change_30d"),
            "ath": u.get("ath_price"), "ath_chg": u.get("percent_from_price_ath"), "circ": c.get("circulating_supply"),
            "total": c.get("total_supply"), "max": c.get("max_supply"), "spark": []}


async def markets(n=150):
    async def fetch():
        attempts = [
            ("coingecko", lambda: _cg_markets(n)),
            ("coinpaprika", lambda: _pap_markets(n)),
        ]
        rows, src = await chain("markets", attempts, cooldown_s=120)
        return {"source": src, "rows": rows}
    return await cached(f"markets:{n}", 90, fetch)


async def _cg_markets(n):
    out = []
    for page in range(1, (n - 1) // 250 + 2):
        d = await get_json(f"{CG}/coins/markets", headers=_cg_headers(), params={
            "vs_currency": "usd", "order": "market_cap_desc", "per_page": min(n, 250), "page": page,
            "sparkline": "true", "price_change_percentage": "1h,24h,7d,30d"})
        out += [_cg_market(c) for c in d]
    return out[:n]


async def _pap_markets(n):
    d = await get_json(f"{PAPRIKA}/tickers", params={"limit": n})
    return [_pap_market(c) for c in d]


async def global_stats():
    async def cg():
        d = (await get_json(f"{CG}/global", headers=_cg_headers()))["data"]
        return {"mcap": d["total_market_cap"]["usd"], "vol": d["total_volume"]["usd"],
                "btc_dom": d["market_cap_percentage"]["btc"], "eth_dom": d["market_cap_percentage"]["eth"],
                "mcap_chg24": d.get("market_cap_change_percentage_24h_usd"),
                "coins": d.get("active_cryptocurrencies")}

    async def pap():
        d = await get_json(f"{PAPRIKA}/global")
        return {"mcap": d["market_cap_usd"], "vol": d["volume_24h_usd"], "btc_dom": d["bitcoin_dominance_percentage"],
                "eth_dom": None, "mcap_chg24": d.get("market_cap_change_24h"),
                "coins": d.get("cryptocurrencies_number")}

    async def fetch():
        data, src = await chain("global", [("coingecko", cg), ("coinpaprika", pap)], cooldown_s=120)
        return {**data, "source": src}
    return await cached("global", 60, fetch)


async def trending():
    async def fetch():
        res = {"coins": [], "dex": [], "sources": []}
        try:
            d = await get_json(f"{CG}/search/trending", headers=_cg_headers())
            for c in d.get("coins", [])[:15]:
                it = c["item"]
                pd = (it.get("data") or {})
                res["coins"].append({"id": it["id"], "sym": it["symbol"].upper(), "name": it["name"],
                                     "rank": it.get("market_cap_rank"), "image": it.get("small"),
                                     "price": pd.get("price"),
                                     "chg24h": ((pd.get("price_change_percentage_24h") or {}).get("usd"))})
            res["sources"].append("coingecko")
            net.HEALTH.ok("trending", "coingecko", 0)
        except Exception as e:  # noqa: BLE001
            net.HEALTH.fail("trending", "coingecko", e)
        try:
            d = await get_json("https://api.dexscreener.com/token-boosts/top/v1")
            for t in d[:15]:
                res["dex"].append({"chain": t.get("chainId"), "address": t.get("tokenAddress"),
                                   "boost": t.get("totalAmount"), "url": t.get("url"),
                                   "desc": (t.get("description") or "")[:140], "icon": t.get("icon")})
            res["sources"].append("dexscreener")
            net.HEALTH.ok("trending", "dexscreener", 0)
        except Exception as e:  # noqa: BLE001
            net.HEALTH.fail("trending", "dexscreener", e)
        return res
    return await cached("trending", 300, fetch)


_cg_ids: dict[str, str] = {}


async def cg_id(base):
    base = base.upper()
    if not _cg_ids:
        try:
            m = await markets(250)
            for r in reversed(m["rows"]):
                _cg_ids[r["sym"]] = r["id"]
        except Exception:  # noqa: BLE001
            pass
    if base in _cg_ids:
        return _cg_ids[base]
    try:
        d = await get_json(f"{CG}/search", headers=_cg_headers(), params={"query": base})
        for c in d.get("coins", []):
            if c["symbol"].upper() == base:
                _cg_ids[base] = c["id"]
                return c["id"]
    except Exception:  # noqa: BLE001
        pass
    return None


async def coin_detail(base):
    """Fundamentals: supply, valuation, links, categories, + DefiLlama TVL/fees if it's a protocol/chain."""
    base = base.upper()

    async def fetch():
        cid = await cg_id(base)
        out = {"sym": base, "id": cid}

        async def cg():
            d = await get_json(f"{CG}/coins/{cid}", headers=_cg_headers(), params={
                "localization": "false", "tickers": "false", "community_data": "true", "developer_data": "true"})
            md = d.get("market_data") or {}
            u = lambda k: (md.get(k) or {}).get("usd")  # noqa: E731
            return {"name": d.get("name"), "categories": d.get("categories") or [],
                    "description": re.sub(r"<[^>]+>", "", (d.get("description") or {}).get("en") or "")[:1200],
                    "homepage": ((d.get("links") or {}).get("homepage") or [None])[0],
                    "genesis": d.get("genesis_date"), "hashing": d.get("hashing_algorithm"),
                    "sentiment_up": d.get("sentiment_votes_up_percentage"),
                    "price": u("current_price"), "mcap": u("market_cap"), "fdv": u("fully_diluted_valuation"),
                    "vol": u("total_volume"), "ath": u("ath"), "ath_date": (md.get("ath_date") or {}).get("usd"),
                    "atl": u("atl"), "circ": md.get("circulating_supply"), "total": md.get("total_supply"),
                    "max": md.get("max_supply"), "chg7d": md.get("price_change_percentage_7d"),
                    "chg30d": md.get("price_change_percentage_30d"), "chg1y": md.get("price_change_percentage_1y"),
                    "dev": {k: (d.get("developer_data") or {}).get(k) for k in
                            ("stars", "commit_count_4_weeks", "pull_requests_merged")},
                    "twitter_followers": (d.get("community_data") or {}).get("twitter_followers")}

        async def pap():
            pid = f"{base.lower()}-"
            lst = await cached("pap:coins", 86400, lambda: get_json(f"{PAPRIKA}/coins"))
            hit = next((c for c in lst if c["symbol"].upper() == base and c.get("is_active")), None)
            if not hit:
                raise net.SourceError(f"no paprika id {pid}")
            d = await get_json(f"{PAPRIKA}/coins/{hit['id']}")
            return {"name": d.get("name"), "categories": [t["name"] for t in d.get("tags", [])][:6],
                    "description": (d.get("description") or "")[:1200], "homepage": None,
                    "genesis": d.get("started_at"), "hashing": d.get("hash_algorithm")}

        attempts = ([("coingecko", cg)] if cid else []) + [("coinpaprika", pap)]
        try:
            info, src = await chain(f"coin:{base}", attempts, cooldown_s=60)
            out.update(info)
            out["source"] = src
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
        out["defi"] = await _llama_match(base, out.get("name"))
        if out.get("mcap") and out.get("fdv"):
            out["mcap_fdv"] = out["mcap"] / out["fdv"]
        if out.get("circ") and out.get("max"):
            out["supply_issued_pct"] = out["circ"] / out["max"] * 100
        return out
    return await cached(f"coin:{base}", 600, fetch)


async def _llama_match(base, name):
    """Find DefiLlama protocol or chain for this token: TVL + fees + revenue (real fundamentals)."""
    try:
        protos = await cached("llama:protocols", 3600, lambda: get_json("https://api.llama.fi/protocols"))
        chains = await cached("llama:chains", 3600, lambda: get_json("https://api.llama.fi/v2/chains"))
    except Exception:  # noqa: BLE001
        return None
    res = {}
    ch = next((c for c in chains if (c.get("tokenSymbol") or "").upper() == base), None)
    if ch:
        res["chain"] = {"name": ch["name"], "tvl": ch.get("tvl")}
    cands = [p for p in protos if (p.get("symbol") or "").upper() == base and p.get("tvl")]
    if cands:
        p = max(cands, key=lambda p: p.get("tvl") or 0)
        res["protocol"] = {"name": p["name"], "slug": p.get("slug"), "category": p.get("category"),
                           "tvl": p.get("tvl"), "chg1d": p.get("change_1d"), "chg7d": p.get("change_7d"),
                           "mcap": p.get("mcap"), "chains": (p.get("chains") or [])[:6]}
        try:
            slug = p.get("slug")
            f = await cached(f"llama:fees:{slug}", 3600, lambda: get_json(
                f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyFees"))
            r = await cached(f"llama:rev:{slug}", 3600, lambda: get_json(
                f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyRevenue"))
            res["protocol"].update({"fees24h": f.get("total24h"), "fees30d": f.get("total30d"),
                                    "rev24h": r.get("total24h"), "rev30d": r.get("total30d")})
            if p.get("mcap") and r.get("total30d"):
                res["protocol"]["pe_annualized"] = p["mcap"] / (r["total30d"] * 12.17)
        except Exception:  # noqa: BLE001
            pass
    return res or None


# ------------------------------------------------------------------ derivatives
async def derivs_universe():
    """Funding (normalised to 8h), OI, 24h change for every linear perp. Bybit -> Binance -> Hyperliquid."""
    async def bybit():
        d = await get_json(f"{BYBIT}/tickers", params={"category": "linear"})
        rows = []
        for t in d["result"]["list"]:
            if not t["symbol"].endswith("USDT"):
                continue
            fr, ih = _f(t.get("fundingRate")), _f(t.get("fundingIntervalHour"), 8) or 8
            oi = _f(t.get("openInterestValue"))
            if fr is None or not oi:
                continue
            rows.append({"sym": t["symbol"][:-4], "price": _f(t["lastPrice"]), "fund8h": fr * 8 / ih * 100,
                         "oi": oi, "chg24": (_f(t.get("price24hPcnt"), 0) or 0) * 100,
                         "vol": _f(t.get("turnover24h")), "basis": None})
        return rows

    async def binance():
        d = await get_json(f"{BFUT}/fapi/v1/premiumIndex")
        tk = {t["symbol"]: t for t in await get_json(f"{BFUT}/fapi/v1/ticker/24hr")}
        rows = []
        for p in d:
            s = p["symbol"]
            if not s.endswith("USDT") or s not in tk:
                continue
            rows.append({"sym": s[:-4], "price": _f(p["markPrice"]), "fund8h": _f(p["lastFundingRate"], 0) * 100,
                         "oi": None, "chg24": _f(tk[s]["priceChangePercent"]), "vol": _f(tk[s]["quoteVolume"]),
                         "basis": None})
        return rows

    async def hyperliquid():
        meta, ctx = await post_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
        rows = []
        for a, c in zip(meta["universe"], ctx):
            px = _f(c.get("markPx"))
            prev = _f(c.get("prevDayPx"))
            if not px:
                continue
            rows.append({"sym": a["name"], "price": px, "fund8h": _f(c.get("funding"), 0) * 8 * 100,
                         "oi": _f(c.get("openInterest"), 0) * px, "chg24": (px / prev - 1) * 100 if prev else None,
                         "vol": _f(c.get("dayNtlVlm")), "basis": None})
        return rows

    async def fetch():
        # fixed order: Bybit is the only one with OI for every perp (completeness beats latency here)
        rows, src = await chain("derivs", [("bybit", bybit), ("binance-futures", binance),
                                           ("hyperliquid", hyperliquid)], cooldown_s=60, adaptive=False)
        rows.sort(key=lambda r: r["oi"] or r["vol"] or 0, reverse=True)
        return {"source": src, "rows": rows}
    return await cached("derivs", 30, fetch)


async def positioning(base):
    """OI history, long/short ratios, taker flow, funding history (Binance futures data)."""
    base = base.upper()
    sym = f"{base}USDT"

    async def fetch():
        out = {"sym": base}
        fd = f"{BFUT}/futures/data"

        async def series(path, key, limit=48, period="1h"):
            d = await get_json(f"{fd}/{path}", params={"symbol": sym, "period": period, "limit": limit})
            return [{"t": int(x["timestamp"]) // 1000, "v": float(x[key])} for x in d]

        tasks = {
            "oi": series("openInterestHist", "sumOpenInterestValue"),
            "ls_retail": series("globalLongShortAccountRatio", "longShortRatio"),
            "ls_top": series("topLongShortPositionRatio", "longShortRatio"),
            "taker": series("takerlongshortRatio", "buySellRatio"),
        }
        res = await asyncio.gather(*tasks.values(), return_exceptions=True)
        ok = False
        for k, v in zip(tasks, res):
            if isinstance(v, Exception):
                out[k] = None
            else:
                out[k] = v
                ok = True
        if ok:
            net.HEALTH.ok("positioning", "binance-futures", 0)
        else:
            net.HEALTH.fail("positioning", "binance-futures", res[0])
            # Bybit fallback for OI + LS
            try:
                d = await get_json(f"{BYBIT}/open-interest", params={"category": "linear", "symbol": sym,
                                                                     "intervalTime": "1h", "limit": 48})
                out["oi"] = [{"t": int(x["timestamp"]) // 1000, "v": float(x["openInterest"])}
                             for x in reversed(d["result"]["list"])]
                d = await get_json(f"{BYBIT}/account-ratio", params={"category": "linear", "symbol": sym,
                                                                     "period": "1h", "limit": 48})
                out["ls_retail"] = [{"t": int(x["timestamp"]) // 1000,
                                     "v": float(x["buyRatio"]) / max(float(x["sellRatio"]), 1e-9)}
                                    for x in reversed(d["result"]["list"])]
                net.HEALTH.ok("positioning", "bybit", 0)
            except Exception as e:  # noqa: BLE001
                net.HEALTH.fail("positioning", "bybit", e)
        try:
            out["funding"] = await funding_history(base, 90)
        except Exception:  # noqa: BLE001
            out["funding"] = None
        if out.get("oi") and len(out["oi"]) > 24:
            a, b = out["oi"][-25]["v"], out["oi"][-1]["v"]
            out["oi_chg24"] = (b / a - 1) * 100 if a else None
        return out
    return await cached(f"pos:{base}", 120, fetch)


async def funding_history(base, limit=500):
    sym = f"{base.upper()}USDT"

    async def bn():
        d = await get_json(f"{BFUT}/fapi/v1/fundingRate", params={"symbol": sym, "limit": min(limit, 1000)})
        return [{"t": int(x["fundingTime"]) // 1000, "v": float(x["fundingRate"]) * 100} for x in d]

    async def bb():
        d = await get_json(f"{BYBIT}/funding/history", params={"category": "linear", "symbol": sym, "limit": 200})
        return [{"t": int(x["fundingRateTimestamp"]) // 1000, "v": float(x["fundingRate"]) * 100}
                for x in reversed(d["result"]["list"])]

    rows, _ = await chain(f"funding:{base}", [("binance-futures", bn), ("bybit", bb)], cooldown_s=60)
    return rows


# ------------------------------------------------------------------ liquidations (live websocket)
class Liquidations:
    def __init__(self):
        self.events = deque(maxlen=400)
        self.listeners = []
        self.source = None
        self.connected = False
        self.since = None

    def add(self, ev):
        self.events.append(ev)
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:  # noqa: BLE001
                pass

    def summary(self, window_s=3600):
        now = time.time()
        agg = {}
        tot = {"long": 0.0, "short": 0.0}
        for e in self.events:
            if now - e["t"] > window_s:
                continue
            s = agg.setdefault(e["sym"], {"sym": e["sym"], "long": 0.0, "short": 0.0})
            s[e["side"]] += e["usd"]
            tot[e["side"]] += e["usd"]
        top = sorted(agg.values(), key=lambda s: s["long"] + s["short"], reverse=True)[:12]
        return {"window_s": window_s, "total": tot, "top": top, "recent": list(self.events)[-40:][::-1],
                "source": self.source, "connected": self.connected,
                "listening_min": round((now - self.since) / 60) if self.since else 0}

    async def run(self):
        while True:
            for name, fn in (("binance-futures", self._binance), ("bybit", self._bybit)):
                try:
                    self.source = name
                    await fn()
                except Exception as e:  # noqa: BLE001
                    net.HEALTH.fail("liquidations", name, e)
                    self.connected = False
                    await asyncio.sleep(3)

    async def _binance(self):
        # NB: only the /market/ws/ path delivers; the legacy /ws/ path connects but stays silent.
        async with websockets.connect("wss://fstream.binance.com/market/ws/!forceOrder@arr",
                                      open_timeout=10, ping_interval=60) as ws:
            self.connected = True
            self.since = self.since or time.time()
            net.HEALTH.ok("liquidations", "binance-futures", 0)
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 600))
                o = m.get("o") or {}
                s = o.get("s", "")
                if not s.endswith("USDT"):
                    continue
                usd = float(o.get("ap") or o.get("p") or 0) * float(o.get("z") or o.get("q") or 0)
                # SELL order = a long being liquidated
                self.add({"t": (o.get("T") or m.get("E", 0)) / 1000, "sym": s[:-4],
                          "side": "long" if o.get("S") == "SELL" else "short", "usd": usd,
                          "price": float(o.get("ap") or o.get("p") or 0), "venue": "binance"})

    async def _bybit(self):
        syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "SUIUSDT", "HYPEUSDT"]
        async with websockets.connect("wss://stream.bybit.com/v5/public/linear", open_timeout=10) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{s}" for s in syms]}))
            self.connected = True
            self.since = self.since or time.time()
            net.HEALTH.ok("liquidations", "bybit", 0)
            last_ping = time.time()
            while True:
                if time.time() - last_ping > 20:
                    await ws.send('{"op":"ping"}')
                    last_ping = time.time()
                m = json.loads(await asyncio.wait_for(ws.recv(), 60))
                for o in m.get("data") or []:
                    s = o.get("s", "")
                    px, q = float(o.get("p", 0)), float(o.get("v", 0))
                    # Bybit: S=Buy means a long position was liquidated
                    self.add({"t": o.get("T", 0) / 1000, "sym": s[:-4], "side": "long" if o.get("S") == "Buy" else "short",
                              "usd": px * q, "price": px, "venue": "bybit"})


LIQS = Liquidations()


# ------------------------------------------------------------------ vol / on-chain / flows / prediction markets
_PM_KW = re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|crypto|stablecoin|microstrategy|coinbase|"
                    r"fed|fomc|interest rate|rate cut|inflation|cpi|recession|s&p|nasdaq|etf|hyperliquid|doge)\b", re.I)

async def dvol():
    async def fetch():
        out = {}
        now = int(time.time() * 1000)
        for cur in ("BTC", "ETH"):
            try:
                d = await get_json("https://www.deribit.com/api/v2/public/get_volatility_index_data", params={
                    "currency": cur, "resolution": "3600", "start_timestamp": now - 30 * 86400000, "end_timestamp": now})
                rows = d["result"]["data"]
                closes = [r[4] for r in rows]
                out[cur] = {"now": closes[-1], "d24": closes[-25] if len(closes) > 25 else None,
                            "lo30": min(closes), "hi30": max(closes),
                            "series": [{"t": r[0] // 1000, "v": r[4]} for r in rows[::4]]}
                net.HEALTH.ok("dvol", "deribit", 0)
            except Exception as e:  # noqa: BLE001
                net.HEALTH.fail("dvol", "deribit", e)
        return out
    return await cached("dvol", 300, fetch)


async def farside(path):
    s = await get_text(f"https://farside.co.uk/{path}/", ua=UA_BROWSER,
                       headers={"Accept": "text/html,application/xhtml+xml"}, timeout=20)
    t = max(re.findall(r"<table.*?</table>", s, re.S), key=len)
    rows = []
    for r in re.findall(r"<tr.*?</tr>", t, re.S):
        c = [html.unescape(re.sub(r"<.*?>", "", x)).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if c and re.match(r"\d{1,2} \w{3} \d{4}", c[0]) and c[-1] not in ("", "-"):
            v = c[-1].replace(",", "")
            try:
                v = -float(v[1:-1]) if v.startswith("(") else float(v)
            except ValueError:
                continue
            rows.append({"date": c[0], "flow": v})
    return rows


async def onchain():
    async def fetch():
        out = {}

        async def stables():
            d = await get_json("https://stablecoins.llama.fi/stablecoincharts/all")
            pts = [{"t": int(x["date"]), "v": x["totalCirculatingUSD"]["peggedUSD"]} for x in d[-120:]]
            return pts

        async def tvl():
            d = await get_json("https://api.llama.fi/v2/historicalChainTvl")
            return [{"t": int(x["date"]), "v": x["tvl"]} for x in d[-120:]]

        async def mempool():
            fees = await get_json("https://mempool.space/api/v1/fees/recommended")
            hr = await get_json("https://mempool.space/api/v1/mining/hashrate/1m")
            return {"fees": fees, "hashrate_eh": (hr.get("currentHashrate") or 0) / 1e18,
                    "difficulty_t": (hr.get("currentDifficulty") or 0) / 1e12}

        async def etf():
            res = {}
            for k in ("btc", "eth"):
                try:
                    res[k] = (await farside(k))[-15:]
                except Exception as e:  # noqa: BLE001
                    res[k] = None
                    net.HEALTH.fail("etf", "farside", e)
            if any(res.values()):
                net.HEALTH.ok("etf", "farside", 0)
            return res

        names = ["stables", "tvl", "mempool", "etf"]
        res = await asyncio.gather(stables(), tvl(), mempool(), etf(), return_exceptions=True)
        for n, r in zip(names, res):
            src = {"stables": "defillama-stables", "tvl": "defillama", "mempool": "mempool.space", "etf": "farside"}[n]
            if isinstance(r, Exception):
                net.HEALTH.fail("onchain", src, r)
                out[n] = None
            else:
                if n != "etf":
                    net.HEALTH.ok("onchain", src, 0)
                out[n] = r
        s = out.get("stables")
        if s and len(s) > 31:
            out["stables_chg7d"] = (s[-1]["v"] / s[-8]["v"] - 1) * 100
            out["stables_chg30d"] = (s[-1]["v"] / s[-31]["v"] - 1) * 100
        out["dvol"] = await dvol()
        return out
    return await cached("onchain", 600, fetch)


async def polymarket():
    async def fetch():
        d = await get_json("https://gamma-api.polymarket.com/markets", params={
            "limit": 200, "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false"})
        out = []
        for m in d:
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            except (ValueError, TypeError):
                continue
            if not outcomes or len(outcomes) != len(prices):
                continue
            if not _PM_KW.search(m.get("question") or ""):
                continue
            # near-certain markets carry no information
            if max(prices) > 0.97 or min(prices) < 0.03:
                continue
            out.append({"q": m.get("question"), "slug": m.get("slug"), "end": m.get("endDate"),
                        "vol24": _f(m.get("volume24hr")), "liq": _f(m.get("liquidity")),
                        "outcomes": [{"name": o, "p": p} for o, p in zip(outcomes, prices)]})
        net.HEALTH.ok("prediction-markets", "polymarket", 0)
        return out[:20]
    try:
        return await cached("polymarket", 300, fetch)
    except Exception as e:  # noqa: BLE001
        net.HEALTH.fail("prediction-markets", "polymarket", e)
        return []
