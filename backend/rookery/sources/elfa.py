"""Elfa: X/Telegram social intelligence, spent under a credit budget.

Plan-aware: the free tier has 1,000 credits/month. Measurement endpoints cost 1 credit, narratives and event
summaries cost 5, chat needs a Grow/PAYG plan. `key-status` is free and is the source of truth for usage.

* Background jobs (trending tokens, trending contract addresses, token news, narratives) run on a cadence and only
  while the daily pace stays inside (monthly allowance - reserve) / days left. Results persist in SQLite so a
  restart never re-spends credits.
* On-demand lookups (top mentions for an asset, event summaries) may dip into the reserve, never past 99%.
* Elfa measures attention, not meaning: no tweet text, no sentiment field. Narratives / event summaries carry text.
"""
import asyncio
import calendar
import json
import logging
import math
import time
from datetime import datetime, timezone

import httpx

from .. import db, net
from ..config import load_settings, secret

log = logging.getLogger("rookery.elfa")
BASE = "https://api.elfa.ai"
COST = {"trending-tokens": 1, "top-mentions": 1, "keyword-mentions": 1, "token-news": 1, "trending-cas": 1,
        "event-summary": 5, "trending-narratives": 5}
STATUS_TTL = 1800


class BudgetExceeded(Exception):
    pass


class PlanError(Exception):
    """The key's plan does not include this endpoint (HTTP 403)."""


def key():
    return secret("ELFA_API_KEY")


def enabled():
    return bool(key()) and load_settings()["elfa"].get("enabled", True)


# ------------------------------------------------------------------ persistence helpers
def kv_get(k):
    r = db.q("SELECT v, ts FROM kv WHERE k=?", (k,))
    return (json.loads(r[0]["v"]), r[0]["ts"]) if r else (None, None)


def kv_put(k, v):
    db.x("INSERT OR REPLACE INTO kv(k, v, ts) VALUES (?,?,?)", (k, json.dumps(v), time.time()))


def _day(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%d")


def _record_spend(units, what):
    db.x("INSERT INTO api_usage(day, api, units) VALUES (?, 'elfa', ?) "
         "ON CONFLICT(day, api) DO UPDATE SET units = units + excluded.units", (_day(), units))
    st, ts = kv_get("elfa:status")
    if st:
        st["local_since"] = st.get("local_since", 0) + units
        db.x("UPDATE kv SET v=? WHERE k='elfa:status'", (json.dumps(st),))
    log.info("elfa spend %d (%s)", units, what)


# ------------------------------------------------------------------ budget
class Budget:
    """Pacing maths. All numbers in credits."""

    @staticmethod
    def status():
        cfg = load_settings()["elfa"]
        now = datetime.now(timezone.utc)
        month_prefix = now.strftime("%Y-%m")
        local_month = sum(r["units"] for r in db.q("SELECT units FROM api_usage WHERE api='elfa' AND day LIKE ?",
                                                   (month_prefix + "%",)))
        today = sum(r["units"] for r in db.q("SELECT units FROM api_usage WHERE api='elfa' AND day=?", (_day(),)))
        remote, rts = kv_get("elfa:status")
        limit = cfg.get("monthly_credits") or 1000
        used = local_month
        source = "local"
        if remote and rts and remote.get("monthly") is not None:
            limit = remote.get("limit_month") or limit
            used = max(remote["monthly"] + remote.get("local_since", 0), 0)
            source = "elfa"
        reserve = limit * (cfg.get("reserve_pct", 8) / 100)
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        days_left = days_in_month - now.day + 1
        used_before_today = max(used - today, 0)
        daily_allowance = max(0.0, (limit - reserve - used_before_today) / days_left)
        return {"limit": limit, "used": used, "today": today, "reserve": round(reserve), "days_left": days_left,
                "daily_allowance": round(daily_allowance, 1), "remaining": max(limit - used, 0), "source": source,
                "tier": (remote or {}).get("tier"), "checked": rts}

    @staticmethod
    def allow(cost, auto):
        b = Budget.status()
        if b["used"] + cost > b["limit"] * 0.99:
            return False, "monthly credits exhausted"
        if auto:
            if b["used"] + cost > b["limit"] - b["reserve"]:
                return False, "only the on-demand reserve is left"
            if b["today"] + cost > b["daily_allowance"]:
                return False, f"daily pace reached ({b['today']}/{b['daily_allowance']:.0f})"
        return True, ""


# ------------------------------------------------------------------ transport
async def _get(path, params, cost_key, *, auto, what=""):
    k = key()
    if not k:
        raise PlanError("no Elfa key configured")
    cost = COST[cost_key]
    ok, why = Budget.allow(cost, auto)
    if not ok:
        raise BudgetExceeded(why)
    t0 = time.perf_counter()
    try:
        r = await net.client().get(f"{BASE}{path}", params=params, headers={"x-elfa-api-key": k}, timeout=20)
    except httpx.HTTPError as e:
        net.HEALTH.fail("elfa", "elfa", e)
        raise
    if r.status_code == 403:
        net.HEALTH.fail("elfa", "elfa", "403 plan")
        raise PlanError(_err_text(r))
    if r.status_code == 429:
        net.HEALTH.fail("elfa", "elfa", "429")
        raise net.SourceError("Elfa rate limited (429)")
    if r.status_code >= 400:
        net.HEALTH.fail("elfa", "elfa", f"HTTP {r.status_code}")
        raise net.SourceError(f"Elfa HTTP {r.status_code}: {_err_text(r)}")
    net.HEALTH.ok("elfa", "elfa", (time.perf_counter() - t0) * 1000)
    _record_spend(cost, what or cost_key)
    d = r.json()
    return d.get("data", d)


def _err_text(r):
    try:
        j = r.json()
        return str(j.get("message") or j.get("error") or j)[:240]
    except Exception:  # noqa: BLE001
        return r.text[:240]


async def refresh_status(force=False):
    """key-status is free: usage + limits straight from Elfa."""
    k = key()
    if not k:
        return None
    cur, ts = kv_get("elfa:status")
    if cur and ts and not force and time.time() - ts < STATUS_TTL:
        return cur
    try:
        r = await net.client().get(f"{BASE}/v2/key-status", headers={"x-elfa-api-key": k}, timeout=10)
        r.raise_for_status()
        d = r.json().get("data", {})
    except Exception as e:  # noqa: BLE001
        net.HEALTH.fail("elfa", "elfa", e)
        return cur
    st = {"monthly": (d.get("usage") or {}).get("monthly"), "daily": (d.get("usage") or {}).get("daily"),
          "limit_month": (d.get("limits") or {}).get("monthly") or d.get("monthlyRequestLimit"),
          "limit_day": (d.get("limits") or {}).get("daily") or d.get("dailyRequestLimit"),
          "tier": d.get("tier"), "status": d.get("status"), "rpm": d.get("requestsPerMinute"),
          "scopes": [s for s in d.get("scopes") or [] if isinstance(s, str)], "local_since": 0}
    kv_put("elfa:status", st)
    return st


# ------------------------------------------------------------------ background jobs
async def _job_trending():
    d = await _get("/v2/aggregations/trending-tokens", {"timeWindow": "24h", "pageSize": 60, "minMentions": 5},
                   "trending-tokens", auto=True, what="trending tokens")
    rows = d.get("data") if isinstance(d, dict) else d
    now = time.time()
    out = []
    for i, r in enumerate(rows or []):
        sym = str(r.get("token", "")).upper().lstrip("$")
        if not sym:
            continue
        out.append({"sym": sym, "rank": i + 1, "cur": int(r.get("current_count") or 0),
                    "prev": int(r.get("previous_count") or 0), "chg": float(r.get("change_percent") or 0)})
    db.xmany("INSERT OR REPLACE INTO elfa_trending(ts, sym, rank, cur, prev, chg) VALUES (?,?,?,?,?,?)",
             [(now, r["sym"], r["rank"], r["cur"], r["prev"], r["chg"]) for r in out])
    db.x("DELETE FROM elfa_trending WHERE ts < ?", (now - 120 * 86400,))
    kv_put("elfa:trending", {"rows": out, "window": "24h"})
    return out


async def _job_cas():
    tw, tg = await asyncio.gather(
        _get("/v2/aggregations/trending-cas/twitter", {"timeWindow": "24h", "pageSize": 30, "minMentions": 5},
             "trending-cas", auto=True, what="trending CAs (X)"),
        _get("/v2/aggregations/trending-cas/telegram", {"timeWindow": "24h", "pageSize": 30, "minMentions": 5},
             "trending-cas", auto=True, what="trending CAs (Telegram)"), return_exceptions=True)
    by = {}
    for src, d in (("x", tw), ("tg", tg)):
        if isinstance(d, BaseException):
            continue
        for r in (d.get("data") if isinstance(d, dict) else d) or []:
            ca = r.get("contractAddress")
            if not ca:
                continue
            e = by.setdefault(ca, {"ca": ca, "chain": r.get("chain") or "", "x": 0, "tg": 0})
            e[src] = int(r.get("mentionCount") or 0)
    if not by:
        raise net.SourceError("no CA data")
    rows = sorted(by.values(), key=lambda e: e["x"] + e["tg"], reverse=True)
    for e in rows:
        # Elfa's "lead time" play: Telegram tends to lead, X confirms. On TG but not (yet) on X = early.
        e["early"] = e["tg"] > 0 and e["x"] == 0
    await _enrich_cas(rows)
    kv_put("elfa:cas", {"rows": rows})
    return rows


async def _enrich_cas(rows):
    """Attach symbol / price / liquidity from Jupiter (Solana) or DexScreener (EVM). Free, no Elfa credits."""
    from . import solana
    sol = [r["ca"] for r in rows if r["chain"] == "solana"]
    try:
        info = await solana.token_info(sol) if sol else {}
    except Exception:  # noqa: BLE001
        info = {}
    for r in rows:
        i = info.get(r["ca"])
        if i:
            r.update({"sym": i.get("symbol"), "name": i.get("name"), "price": i.get("price"),
                      "liquidity": i.get("liquidity"), "mcap": i.get("mcap"), "verified": i.get("verified"),
                      "organic": i.get("organic"), "chg24": i.get("chg24")})
    evm = [r for r in rows if r["chain"] not in ("solana", "") and not r.get("sym")]
    for chain_name in {r["chain"] for r in evm}:
        addrs = [r["ca"] for r in evm if r["chain"] == chain_name][:30]
        try:
            d = await net.get_json(f"https://api.dexscreener.com/tokens/v1/{chain_name}/{','.join(addrs)}")
        except Exception:  # noqa: BLE001
            continue
        best = {}
        for p in d or []:
            a = (p.get("baseToken") or {}).get("address", "")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if a and liq >= (best.get(a.lower(), {}).get("liq") or 0):
                best[a.lower()] = {"liq": liq, "p": p}
        for r in evm:
            b = best.get(r["ca"].lower())
            if b:
                p = b["p"]
                r.update({"sym": p["baseToken"].get("symbol"), "name": p["baseToken"].get("name"),
                          "price": float(p.get("priceUsd") or 0) or None, "liquidity": b["liq"],
                          "mcap": p.get("marketCap"), "chg24": (p.get("priceChange") or {}).get("h24")})


async def _job_news():
    d = await _get("/v2/data/token-news", {"timeWindow": "24h", "pageSize": 30}, "token-news", auto=True,
                   what="token news")
    rows = [_mention(m) for m in (d if isinstance(d, list) else d.get("data") or [])]
    kv_put("elfa:news", {"rows": rows})
    return rows


async def _job_narratives():
    d = await _get("/v2/data/trending-narratives", {"timeFrame": "day", "maxNarratives": 8,
                                                    "maxTweetsPerNarrative": 3}, "trending-narratives", auto=True,
                   what="narratives")
    rows = [{"text": n.get("narrative"), "links": (n.get("source_links") or [])[:3]}
            for n in (d.get("trending_narratives") or []) if n.get("narrative")]
    kv_put("elfa:narratives", {"rows": rows})
    return rows


JOBS = {"trending": _job_trending, "cas": _job_cas, "news": _job_news, "narratives": _job_narratives}
JOB_COST = {"trending": 1, "cas": 2, "news": 1, "narratives": 5}


def job_state():
    cfg = load_settings()["elfa"]["cadence_h"]
    out = {}
    for name in JOBS:
        _, ts = kv_get(f"elfa:{name}")
        err, _ = kv_get(f"elfa:{name}:err")
        h = cfg.get(name) or 0
        out[name] = {"cadence_h": h, "last": ts, "cost": JOB_COST[name],
                     "next": (ts + h * 3600) if ts and h else None, "error": err}
    return out


async def run_due():
    """Run every job whose cadence elapsed and whose cost fits the budget. Returns names that ran."""
    if not enabled():
        return []
    await refresh_status()
    ran = []
    for name, st in job_state().items():
        if not st["cadence_h"]:
            continue
        if st["last"] and time.time() - st["last"] < st["cadence_h"] * 3600:
            continue
        ok, why = Budget.allow(JOB_COST[name], auto=True)
        if not ok:
            kv_put(f"elfa:{name}:err", f"paused: {why}")
            continue
        try:
            await JOBS[name]()
            db.x("DELETE FROM kv WHERE k=?", (f"elfa:{name}:err",))
            ran.append(name)
        except (PlanError, BudgetExceeded, net.SourceError, httpx.HTTPError) as e:
            kv_put(f"elfa:{name}:err", str(e)[:200])
            if isinstance(e, PlanError):  # don't hammer an endpoint the plan lacks
                kv_put(f"elfa:{name}", None)
    return ran


# ------------------------------------------------------------------ reads (no credits)
def trending():
    v, ts = kv_get("elfa:trending")
    return {"rows": (v or {}).get("rows", []), "ts": ts}


def cas():
    v, ts = kv_get("elfa:cas")
    return {"rows": (v or {}).get("rows", []), "ts": ts}


def news():
    v, ts = kv_get("elfa:news")
    return {"rows": (v or {}).get("rows", []), "ts": ts}


def narratives():
    v, ts = kv_get("elfa:narratives")
    return {"rows": (v or {}).get("rows", []), "ts": ts}


def x_attention(sym):
    """Latest X mention stats for a symbol + 7d history from stored trending snapshots."""
    sym = sym.upper()
    last = db.q("SELECT MAX(ts) ts FROM elfa_trending")[0]["ts"]
    if not last:
        return None
    cur = db.q("SELECT * FROM elfa_trending WHERE ts=? AND sym=?", (last, sym))
    hist = db.q("SELECT ts, cur, chg, rank FROM elfa_trending WHERE sym=? AND ts > ? ORDER BY ts",
                (sym, time.time() - 7 * 86400))
    if not cur:
        return {"sym": sym, "listed": False, "ts": last, "hist": hist}
    c = cur[0]
    return {"sym": sym, "listed": True, "ts": last, "rank": c["rank"], "mentions": c["cur"], "prev": c["prev"],
            "chg": c["chg"], "hist": hist}


def features(sym):
    """Model features from the latest snapshot (if < 8h old): log mentions, clipped % change, presence."""
    a = x_attention(sym)
    if not a or time.time() - a["ts"] > 8 * 3600:
        return {"x_mentions": 0.0, "x_chg": 0.0, "x_listed": 0.0}
    if not a["listed"]:
        return {"x_mentions": 0.0, "x_chg": 0.0, "x_listed": 0.0}
    return {"x_mentions": math.log1p(a["mentions"]) / 8, "x_chg": max(-1.0, min(3.0, a["chg"] / 100)),
            "x_listed": 1.0}


# ------------------------------------------------------------------ on demand (may use the reserve)
def _mention(m):
    acc = m.get("account") or {}
    rb = m.get("repostBreakdown") or {}
    return {"link": m.get("link"), "ts": m.get("mentionedAt"), "type": m.get("type"),
            "user": acc.get("username"), "verified": acc.get("isVerified"),
            "likes": m.get("likeCount") or 0, "reposts": m.get("repostCount") or 0,
            "views": m.get("viewCount") or 0, "replies": m.get("replyCount") or 0,
            "smart_reposts": rb.get("smart") or 0, "ct_reposts": rb.get("ct") or 0}


async def top_mentions(sym, *, fetch=True, ttl=3 * 3600):
    """Most-engaged X posts for a ticker over 24h (1 credit, cached 3h, persisted)."""
    sym = sym.upper()
    k = f"elfa:top:{sym}"
    v, ts = kv_get(k)
    if v is not None and ts and time.time() - ts < ttl:
        return {**v, "ts": ts, "cached": True}
    if not fetch:
        return {"rows": (v or {}).get("rows", []), "ts": ts, "cached": True, "stale": bool(v)}
    d = await _get("/v2/data/top-mentions", {"ticker": sym, "timeWindow": "24h", "pageSize": 10},
                   "top-mentions", auto=False, what=f"top mentions {sym}")
    rows = [_mention(m) for m in (d if isinstance(d, list) else d.get("data") or [])]
    rows.sort(key=lambda r: r["views"] or r["likes"], reverse=True)
    kv_put(k, {"rows": rows})
    return {"rows": rows, "ts": time.time(), "cached": False}


async def event_summary(keywords, *, ttl=6 * 3600):
    """AI summaries of what X is saying about keywords over 24h (5 credits, cached 6h)."""
    kw = ",".join(sorted({w.strip().lower() for w in keywords if w and w.strip()}))[:200]
    k = f"elfa:events:{kw}"
    v, ts = kv_get(k)
    if v is not None and ts and time.time() - ts < ttl:
        return {**v, "ts": ts, "cached": True}
    d = await _get("/v2/data/event-summary", {"keywords": kw, "timeWindow": "24h", "searchType": "or"},
                   "event-summary", auto=False, what=f"event summary {kw}")
    rows = [{"text": e.get("summary"), "links": (e.get("sourceLinks") or [])[:3]}
            for e in (d if isinstance(d, list) else d.get("data") or []) if e.get("summary")]
    kv_put(k, {"rows": rows, "keywords": kw})
    return {"rows": rows, "keywords": kw, "ts": time.time(), "cached": False}


# ------------------------------------------------------------------ chat ("Ask Elfa" as an AI model)
CHAT_MODELS = {"elfa-fast": "fast", "elfa-expert": "expert", "elfa-adaptive": "adaptive"}
_sessions: dict[str, str] = {}


def chat_available():
    v, ts = kv_get("elfa:chat:forbidden")
    if v and ts and time.time() - ts < 24 * 3600:
        return False, v
    if not key():
        return False, "no Elfa key"
    st, _ = kv_get("elfa:status")
    if (st or {}).get("tier") == "free":  # chat is Grow+/PAYG only; don't waste a round-trip per question
        return False, "Elfa chat needs a Grow or Pay-as-you-go plan (this key is free tier)"
    return True, ""


async def chat_stream(message, model="elfa-fast", thread=None):
    """Yields {"type": "delta"|"status", ...}. Tries SSE (PAYG/Enterprise), then JSON (Grow+).
    The live-data context is NOT sent: Elfa answers from its own data (keeps portfolio info local)."""
    k = key()
    if not k:
        raise PlanError("no Elfa key configured")
    ok, why = chat_available()
    if not ok:
        raise PlanError(f"Elfa chat unavailable: {why}")
    body = {"analysisType": "chat", "message": message, "speed": CHAT_MODELS.get(model, "fast")}
    if thread and thread in _sessions:
        body["sessionId"] = _sessions[thread]
    headers = {"x-elfa-api-key": k, "Content-Type": "application/json"}
    timeout = httpx.Timeout(connect=6, read=90, write=10, pool=5)
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream("POST", f"{BASE}/v2/chat/stream", json=body, headers=headers) as r:
            if r.status_code == 200:
                async for ev in _parse_sse(r.aiter_lines(), thread):
                    yield ev
                return
            first_err = (await r.aread()).decode(errors="ignore")[:200]
            if r.status_code not in (403, 404):
                raise RuntimeError(f"Elfa chat HTTP {r.status_code}: {first_err}")
        # streaming is PAYG-only; Grow plans can still use the JSON endpoint
        r = await c.post(f"{BASE}/v2/chat", json=body, headers=headers)
        if r.status_code == 403:
            msg = _err_text(r)
            kv_put("elfa:chat:forbidden", msg)
            raise PlanError(msg)
        if r.status_code >= 400:
            raise RuntimeError(f"Elfa chat HTTP {r.status_code}: {_err_text(r)}")
        d = r.json().get("data") or {}
        if d.get("sessionId") and thread:
            _sessions[thread] = d["sessionId"]
        if d.get("creditsConsumed"):
            _record_spend(int(d["creditsConsumed"]), "chat")
        yield {"type": "delta", "text": d.get("message") or ""}


async def _parse_sse(lines, thread=None):
    async for line in lines:
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            j = json.loads(data)
        except json.JSONDecodeError:
            continue
        t = j.get("type")
        if t == "text" and j.get("content"):
            yield {"type": "delta", "text": j["content"]}
        elif t == "status":
            yield {"type": "status", "text": str(j.get("message") or j.get("status") or j.get("content") or "")[:160]}
        elif t == "session_info" and thread and j.get("sessionId"):
            _sessions[thread] = j["sessionId"]
        elif t == "complete":
            if j.get("creditsConsumed"):
                _record_spend(int(j["creditsConsumed"]), "chat")
        elif t in ("error", "invalid_request"):
            raise RuntimeError(f"Elfa {t}: {str(j.get('message') or j.get('error') or j)[:200]}")


def status():
    ok, why = chat_available()
    st, ts = kv_get("elfa:status")
    return {"configured": bool(key()), "enabled": enabled(), "budget": Budget.status(), "jobs": job_state(),
            "chat": {"available": ok, "reason": why}, "tier": (st or {}).get("tier"),
            "scopes": (st or {}).get("scopes", [])}
