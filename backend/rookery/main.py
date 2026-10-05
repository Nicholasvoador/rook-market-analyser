"""Rook Market Analyser backend (package `rookery`): FastAPI app, background schedulers, WebSocket hub, SPA hosting."""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

import orjson
from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import ai, alerts, db, net, portfolio, predict, setups, signals, wallets
from .config import APP_NAME, FRONTEND_DIST, load_settings, save_settings, secret, secrets_status, set_secrets
from .sources import crypto, elfa, markets, sentiment, solana, stocks

log = logging.getLogger("rookery")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _noisy in ("httpx", "httpcore", "websockets"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class ORJSON(Response):
    media_type = "application/json"

    def render(self, content):
        return orjson.dumps(content, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)


# ------------------------------------------------------------------ websocket hub
class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def send(self, msg):
        dead = []
        data = orjson.dumps(msg).decode()
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


HUB = Hub()
STATE = {"started": time.time(), "tasks": {}}


def _task_ok(name, **kw):
    STATE["tasks"][name] = {"last_ok": time.time(), **kw}


def _task_err(name, e):
    STATE["tasks"].setdefault(name, {})
    STATE["tasks"][name].update({"last_error": str(e)[:300], "last_error_ts": time.time()})
    log.warning("%s failed: %s", name, e)


# ------------------------------------------------------------------ background loops
async def loop_news():
    last_hist = 0
    try:
        st = load_settings()
        await sentiment.retag(st["watchlist"]["crypto"] + st["watchlist"]["stocks"])
    except Exception as e:  # noqa: BLE001
        _task_err("retag", e)
    while True:
        st = load_settings()
        try:
            n = await sentiment.ingest(st["watchlist"]["crypto"] + st["watchlist"]["stocks"])
            if time.time() - last_hist > 3000:
                await asyncio.to_thread(sentiment.record_hist)
                last_hist = time.time()
            _task_ok("news", new=n)
            if n:
                await HUB.send({"type": "news", "items": sentiment.news(limit=min(n, 10))})
        except Exception as e:  # noqa: BLE001
            _task_err("news", e)
        await asyncio.sleep(max(60, st["data"]["news_refresh_s"]))


async def loop_market():
    last_snap = 0
    while True:
        st = load_settings()
        try:
            await asyncio.gather(crypto.global_stats(), crypto.derivs_universe(), crypto.markets(150),
                                 stocks.macro(st["macro"]), crypto.tickers(st["watchlist"]["crypto"]),
                                 return_exceptions=True)
            if time.time() - last_snap > 3500:
                snap = {"global": net.peek("global"), "fng": net.peek("fng"),
                        "agg": (await asyncio.to_thread(sentiment.aggregate))["rows"][:40]}
                await asyncio.to_thread(db.put_snapshot, snap)
                last_snap = time.time()
            _task_ok("market")
        except Exception as e:  # noqa: BLE001
            _task_err("market", e)
        await asyncio.sleep(max(20, st["data"]["market_refresh_s"]))


async def loop_predict():
    await asyncio.sleep(5)
    st = load_settings()["predict"]
    status = predict.models_status(st["symbols"], st["horizons_h"])
    last_train = max([(s["meta"] or {}).get("trained_at", 0) for s in status] or [0])
    while True:
        st = load_settings()["predict"]
        if not st["enabled"]:
            await asyncio.sleep(300)
            continue
        try:
            missing = [s for s in predict.models_status(st["symbols"], st["horizons_h"]) if not s["trained"]]
            if missing or time.time() - last_train > st["retrain_every_h"] * 3600:
                await HUB.send({"type": "status", "text": "Retraining forecast models…"})
                res = await predict.retrain(st["symbols"], st["horizons_h"])
                last_train = time.time()
                _task_ok("retrain", results=[{k: r.get(k) for k in ("sym", "horizon_h", "skill_oos", "error")} for r in res])
            g = await predict.grade()
            iss = await predict.issue(st["symbols"], st["horizons_h"])
            _task_ok("predict", graded=g, issued=len([i for i in iss if "p" in i]))
            if g or iss:
                await HUB.send({"type": "predictions", "current": predict.current()})
        except Exception as e:  # noqa: BLE001
            _task_err("predict", e)
        # wake ~70s after the next hour boundary (bars close on the hour)
        now = time.time()
        await asyncio.sleep(max(30, (now // 3600 + 1) * 3600 + 70 - now) if now % 3600 > 140 else 60)


async def loop_alerts():
    await asyncio.sleep(20)
    while True:
        try:
            fired = await alerts.evaluate(HUB.send)
            _task_ok("alerts", fired=len(fired))
        except Exception as e:  # noqa: BLE001
            _task_err("alerts", e)
        await asyncio.sleep(60)


async def loop_brief():
    await asyncio.sleep(90)
    while True:
        h = load_settings()["ai"].get("auto_brief_hours") or 0
        if h <= 0:
            await asyncio.sleep(600)
            continue
        last = db.q("SELECT ts FROM briefs ORDER BY id DESC LIMIT 1")
        due = not last or time.time() - last[0]["ts"] > h * 3600
        if due:
            try:
                b = await ai.brief()
                _task_ok("brief", model=b["model"])
                await HUB.send({"type": "brief", **b})
            except Exception as e:  # noqa: BLE001
                _task_err("brief", e)
        await asyncio.sleep(600)


async def loop_probe():
    """Latency prober: keeps per-source latency fresh so fallback chains try the fastest source first."""
    while True:
        st = load_settings()
        net.ADAPTIVE["on"] = bool(st["data"].get("adaptive_latency", True))
        extra = solana.rpc_probes()
        url = secret("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/")
        extra["hermes"] = ("GET", f"{url}/health", None)
        try:
            await net.probe_once(extra)
            _task_ok("probe")
        except Exception as e:  # noqa: BLE001
            _task_err("probe", e)
        await asyncio.sleep(60)


async def loop_markets():
    """Keeps the cross-asset overview and BCB rates warm so the Markets page opens instantly."""
    await asyncio.sleep(20)
    while True:
        try:
            await markets.cross()
            await markets.brazil()
            _task_ok("markets")
        except Exception as e:  # noqa: BLE001
            _task_err("markets", e)
        await asyncio.sleep(600)


async def loop_elfa():
    await asyncio.sleep(15)
    while True:
        try:
            ran = await elfa.run_due()
            _task_ok("elfa", ran=ran)
            if ran:
                await HUB.send({"type": "elfa", "ran": ran})
        except Exception as e:  # noqa: BLE001
            _task_err("elfa", e)
        await asyncio.sleep(600)


async def loop_signals():
    """Every 15 min: scan the watchlist, log new setup onsets, grade matured ones, warm the stats cache."""
    await asyncio.sleep(45)
    while True:
        st = load_settings()
        try:
            sigs = await signals.scan(st["watchlist"]["crypto"], st["watchlist"]["stocks"], deep=True)
            logged = sum(signals.track(s) for s in sigs)
            graded = await signals.grade_events()
            _task_ok("signals", scanned=len(sigs), logged=logged, graded=graded)
        except Exception as e:  # noqa: BLE001
            _task_err("signals", e)
        await asyncio.sleep(900)


async def loop_liq_broadcast():
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    crypto.LIQS.listeners.append(lambda ev: q.full() or q.put_nowait(ev))
    while True:
        batch = [await q.get()]
        await asyncio.sleep(0.5)
        while not q.empty():
            batch.append(q.get_nowait())
        await HUB.send({"type": "liq", "events": batch})


@asynccontextmanager
async def lifespan(app):
    db.init()
    tasks = [asyncio.create_task(c()) for c in (loop_news, loop_market, loop_predict, loop_alerts, loop_brief,
                                                loop_liq_broadcast, loop_probe, loop_elfa, loop_signals, loop_markets)]
    tasks.append(asyncio.create_task(crypto.LIQS.run()))
    STATE["bg"] = tasks
    yield
    for t in tasks:
        t.cancel()
    await net.close()


app = FastAPI(title=APP_NAME, lifespan=lifespan, default_response_class=ORJSON)


def _err(e):
    raise HTTPException(status_code=502, detail=str(e)[:400])


# ------------------------------------------------------------------ status / settings
@app.get("/api/status")
async def status():
    return {"app": APP_NAME, "uptime_s": time.time() - STATE["started"], "tasks": STATE["tasks"],
            "hermes": await ai.health(), "ws_clients": len(HUB.clients), "liq_connected": crypto.LIQS.connected,
            "elfa": {"configured": bool(elfa.key()), "budget": elfa.Budget.status()}}


@app.get("/api/settings")
async def get_settings():
    return {"settings": load_settings(), "secrets": secrets_status()}


@app.put("/api/settings")
async def put_settings(patch: dict = Body(...)):
    return {"settings": save_settings(patch), "secrets": secrets_status()}


@app.put("/api/secrets")
async def put_secrets(values: dict = Body(...)):
    try:
        set_secrets(values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"secrets": secrets_status()}


@app.get("/api/sources/health")
async def sources_health():
    return net.HEALTH.snapshot()


@app.get("/api/latency")
async def latency():
    return {"probes": net.latency_report(), "venues": crypto.venue_order(), "adaptive": net.ADAPTIVE["on"]}


# ------------------------------------------------------------------ watchlist helpers
@app.post("/api/watchlist")
async def wl_add(body: dict = Body(...)):
    st = load_settings()
    wl = st["watchlist"]
    kind = body.get("kind", "crypto")
    if kind == "dex":
        mint = (body.get("mint") or "").strip()
        if not solana.valid_address(mint):
            raise HTTPException(400, "invalid mint address")
        if not any(d.get("mint") == mint for d in wl["dex"]):
            wl["dex"].append({"chain": "solana", "mint": mint, "sym": (body.get("sym") or mint[:6])[:16],
                              "name": (body.get("name") or "")[:60]})
    else:
        key = "crypto" if kind == "crypto" else "stocks"
        sym = (body.get("sym") or "").strip().upper()
        if not sym or len(sym) > 16:
            raise HTTPException(400, "invalid symbol")
        if sym not in wl[key]:
            wl[key].append(sym)
    return {"settings": save_settings({"watchlist": wl})}


@app.delete("/api/watchlist")
async def wl_del(kind: str, sym: str):
    wl = load_settings()["watchlist"]
    if kind == "dex":
        wl["dex"] = [d for d in wl["dex"] if d.get("mint") != sym]
    else:
        key = "crypto" if kind == "crypto" else "stocks"
        wl[key] = [s for s in wl[key] if s != sym.upper()]
    return {"settings": save_settings({"watchlist": wl})}


# ------------------------------------------------------------------ Elfa
@app.get("/api/elfa/status")
async def elfa_status():
    await elfa.refresh_status()
    return elfa.status()


@app.get("/api/elfa/overview")
async def elfa_overview():
    return {"trending": elfa.trending(), "narratives": elfa.narratives(), "cas": elfa.cas(), "news": elfa.news(),
            "status": elfa.status()}


@app.get("/api/elfa/x")
async def elfa_x(sym: str):
    return elfa.x_attention(sym)


@app.get("/api/elfa/mentions")
async def elfa_mentions(sym: str, fetch: bool = False):
    try:
        return await elfa.top_mentions(sym, fetch=fetch)
    except (elfa.BudgetExceeded, elfa.PlanError) as e:
        raise HTTPException(429 if isinstance(e, elfa.BudgetExceeded) else 403, str(e))
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.post("/api/elfa/events")
async def elfa_events(body: dict = Body(...)):
    kws = body.get("keywords") or []
    if not kws:
        raise HTTPException(400, "keywords required")
    try:
        return await elfa.event_summary(kws)
    except (elfa.BudgetExceeded, elfa.PlanError) as e:
        raise HTTPException(429 if isinstance(e, elfa.BudgetExceeded) else 403, str(e))
    except Exception as e:  # noqa: BLE001
        _err(e)


# ------------------------------------------------------------------ wallets (addresses stay in the local DB)
@app.get("/api/wallets")
async def w_list():
    return wallets.list_wallets()


@app.post("/api/wallets")
async def w_add(body: dict = Body(...)):
    try:
        wid = wallets.add(body.get("address", ""), body.get("label", ""), body.get("chain", "solana"),
                          body.get("include", True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": wid}


@app.put("/api/wallets/{wid}")
async def w_upd(wid: int, body: dict = Body(...)):
    wallets.update(wid, **{k: v for k, v in body.items() if k in ("label", "include")})
    return {"ok": True}


@app.delete("/api/wallets/{wid}")
async def w_del(wid: int):
    wallets.delete(wid)
    return {"ok": True}


@app.get("/api/wallets/{wid}/snapshot")
async def w_snap(wid: int, force: bool = False):
    w = db.q("SELECT * FROM wallets WHERE id=?", (wid,))
    if not w:
        raise HTTPException(404, "wallet not found")
    try:
        return await wallets.snapshot(w[0], force)
    except Exception as e:  # noqa: BLE001
        _err(e)


# ------------------------------------------------------------------ DEX (on-chain) tokens
@app.get("/api/dex/token")
async def dex_token(mint: str):
    if not solana.valid_address(mint):
        raise HTTPException(400, "invalid mint")
    info, pool = await asyncio.gather(solana.token_info([mint]), solana.top_pool(mint), return_exceptions=True)
    i = None if isinstance(info, BaseException) else info.get(mint)
    if not i:
        raise HTTPException(404, "token not found on Jupiter")
    return {**i, "pool": None if isinstance(pool, BaseException) else pool}


@app.get("/api/dex/klines")
async def dex_klines(mint: str, interval: str = "1h", limit: int = Query(500, le=1000)):
    try:
        return await solana.dex_klines(mint, interval, limit)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/dex/prices")
async def dex_prices(mints: str):
    ok = [m for m in dict.fromkeys(x.strip() for x in mints.split(",")) if solana.valid_address(m)][:100]
    if not ok:
        raise HTTPException(400, "no valid mint addresses")
    return await solana.prices(ok)


# ------------------------------------------------------------------ crypto
@app.get("/api/crypto/resolve")
async def c_resolve(sym: str):
    r = await crypto.resolve(sym)
    if not r:
        raise HTTPException(404, f"{sym} not found on any venue")
    return r


@app.get("/api/crypto/klines")
async def c_klines(sym: str, interval: str = "1h", limit: int = Query(500, le=1000)):
    try:
        return await crypto.klines(sym, interval, limit)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/crypto/tickers")
async def c_tickers(syms: str | None = None):
    lst = syms.split(",") if syms else load_settings()["watchlist"]["crypto"]
    return await crypto.tickers(lst)


@app.get("/api/crypto/markets")
async def c_markets(n: int = Query(150, le=250)):
    try:
        return await crypto.markets(n)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/crypto/global")
async def c_global():
    try:
        return await crypto.global_stats()
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/crypto/trending")
async def c_trending():
    return await crypto.trending()


@app.get("/api/crypto/derivs")
async def c_derivs(n: int = 60):
    try:
        d = await crypto.derivs_universe()
        return {"source": d["source"], "rows": d["rows"][:n]}
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/crypto/positioning")
async def c_pos(sym: str):
    return await crypto.positioning(sym)


@app.get("/api/crypto/coin")
async def c_coin(sym: str):
    return await crypto.coin_detail(sym)


@app.get("/api/crypto/liquidations")
async def c_liq(window: int = 3600):
    return crypto.LIQS.summary(window)


@app.get("/api/crypto/onchain")
async def c_onchain():
    return await crypto.onchain()


@app.get("/api/crypto/polymarket")
async def c_poly():
    return await crypto.polymarket()


# ------------------------------------------------------------------ markets (cross-asset boards)
@app.get("/api/markets/board")
async def markets_board(tab: str = "stocks"):
    if tab not in markets.TABS:
        raise HTTPException(400, f"tab must be one of {', '.join(markets.TABS)}")
    try:
        return await markets.board(tab)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/markets/overview")
async def markets_overview():
    cross, mov = await asyncio.gather(markets.cross(), markets.movers(), return_exceptions=True)
    if isinstance(cross, BaseException):
        _err(cross)
    return {**cross, "movers": None if isinstance(mov, BaseException) else mov}


@app.get("/api/markets/row")
async def markets_row(sym: str, kind: str = "stock"):
    if not sym or len(sym) > 24:
        raise HTTPException(400, "invalid symbol")
    r = await markets._safe(markets.row(sym if kind != "crypto" else sym.upper(), kind))
    if not r:
        raise HTTPException(404, "no data for symbol")
    return r


@app.get("/api/markets/universe")
async def markets_universe():
    return markets.universe()


@app.get("/api/markets/brazil")
async def markets_brazil():
    try:
        return markets.public_brazil(await markets.brazil())
    except Exception as e:  # noqa: BLE001
        _err(e)


# ------------------------------------------------------------------ stocks / macro
@app.get("/api/stocks/quotes")
async def s_quotes(syms: str | None = None):
    lst = syms.split(",") if syms else load_settings()["watchlist"]["stocks"]
    try:
        return await stocks.quotes(lst)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/stocks/klines")
async def s_klines(sym: str, interval: str = "1d"):
    try:
        return await stocks.klines(sym, interval)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/stocks/fundamentals")
async def s_fund(sym: str):
    try:
        return await stocks.fundamentals(sym)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/macro")
async def macro():
    return await stocks.macro(load_settings()["macro"])


# ------------------------------------------------------------------ sentiment / news / signals
@app.get("/api/sentiment/overview")
async def sent_overview():
    fng, elfa = await asyncio.gather(sentiment.crypto_fear_greed(), sentiment.elfa_trending(), return_exceptions=True)
    return {"fng": None if isinstance(fng, BaseException) else fng, "mood": sentiment.market_mood(),
            "mindshare": sentiment.aggregate(), "elfa": None if isinstance(elfa, BaseException) else elfa}


@app.get("/api/sentiment/symbol")
async def sent_symbol(sym: str, days: int = 14):
    return {"hist": sentiment.hist(sym, days), "news": sentiment.news(sym=sym, limit=40, hours=days * 24)}


@app.get("/api/news")
async def news(sym: str | None = None, kind: str | None = None, limit: int = 60):
    return sentiment.news(sym=sym, kind=kind, limit=limit)


@app.get("/api/signals")
async def sigs(syms: str | None = None, kind: str = "crypto", deep: bool = False):
    st = load_settings()
    if syms:
        lst = syms.split(",")
        return await (signals.scan(lst, [], deep) if kind == "crypto" else signals.scan([], lst, deep))
    return await signals.scan(st["watchlist"]["crypto"], st["watchlist"]["stocks"], deep)


@app.get("/api/signals/one")
async def sig_one(sym: str, kind: str = "crypto"):
    try:
        if kind == "crypto":
            return await signals.crypto_signal(sym, with_pos=True)
        if kind == "dex":
            return await signals.dex_signal(sym)
        return await signals.stock_signal(sym)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/signals/record")
async def sig_record():
    return signals.live_record()


@app.get("/api/signals/catalog")
async def sig_catalog():
    return {k: {"label": v[0], "dir": v[1], "explain": v[2]} for k, v in setups.SETUPS.items()}


# ------------------------------------------------------------------ predictions
@app.get("/api/predictions/current")
async def pred_current():
    return predict.current()


@app.get("/api/predictions/scoreboard")
async def pred_score():
    st = load_settings()["predict"]
    return {**predict.scoreboard(), "models": predict.models_status(st["symbols"], st["horizons_h"]),
            "llm": predict.llm_track_record()}


@app.get("/api/predictions/history")
async def pred_hist(sym: str, h: int | None = None):
    return predict.history_for(sym.upper(), h)


@app.post("/api/predictions/retrain")
async def pred_retrain():
    st = load_settings()["predict"]

    async def job():
        res = await predict.retrain(st["symbols"], st["horizons_h"])
        await predict.issue(st["symbols"], st["horizons_h"])
        await HUB.send({"type": "predictions", "current": predict.current(), "retrained": len(res)})
    asyncio.create_task(job())
    return {"started": True}


# ------------------------------------------------------------------ alerts
@app.get("/api/alerts")
async def a_list():
    return {"alerts": alerts.list_alerts(), "events": alerts.events(60)}


@app.post("/api/alerts")
async def a_create(spec: dict = Body(...)):
    return {"id": alerts.create(spec)}


@app.post("/api/alerts/parse")
async def a_parse(body: dict = Body(...)):
    try:
        return await ai.parse_alert(body["text"])
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.put("/api/alerts/{aid}")
async def a_update(aid: int, body: dict = Body(...)):
    alerts.update(aid, enabled=body.get("enabled"), spec=body.get("spec"))
    return {"ok": True}


@app.delete("/api/alerts/{aid}")
async def a_delete(aid: int):
    alerts.delete(aid)
    return {"ok": True}


# ------------------------------------------------------------------ portfolio
@app.get("/api/portfolio")
async def pf():
    try:
        return await portfolio.analytics()
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.post("/api/portfolio")
async def pf_add(h: dict = Body(...)):
    return {"id": portfolio.add(h["symbol"], h.get("kind", "crypto"), h["qty"], h.get("avg_cost", 0),
                                h.get("currency", "USD"), h.get("note", ""))}


@app.put("/api/portfolio/{hid}")
async def pf_upd(hid: int, h: dict = Body(...)):
    portfolio.update(hid, **h)
    return {"ok": True}


@app.delete("/api/portfolio/{hid}")
async def pf_del(hid: int):
    portfolio.delete(hid)
    return {"ok": True}


# ------------------------------------------------------------------ AI
@app.get("/api/ai/models")
async def ai_models():
    try:
        return await ai.model_options()
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.post("/api/ai/chat")
async def ai_chat(body: dict = Body(...)):
    async def gen():
        try:
            async for ev in ai.chat_stream(body.get("chat_id"), body["message"], body.get("mode", "fast"),
                                           body.get("focus") or []):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/ai/chats")
async def ai_chats():
    return db.q("SELECT * FROM chats ORDER BY updated DESC LIMIT 100")


@app.get("/api/ai/chats/{cid}")
async def ai_chat_get(cid: str):
    msgs = ai.chat_messages(cid)
    for m in msgs:
        m["meta"] = json.loads(m["meta"] or "{}")
    return {"id": cid, "messages": msgs}


@app.delete("/api/ai/chats/{cid}")
async def ai_chat_del(cid: str):
    db.x("DELETE FROM chat_messages WHERE chat_id=?", (cid,))
    db.x("DELETE FROM chats WHERE id=?", (cid,))
    return {"ok": True}


@app.get("/api/ai/brief")
async def ai_brief_get():
    r = db.q("SELECT * FROM briefs ORDER BY id DESC LIMIT 1")
    return r[0] if r else None


@app.post("/api/ai/brief")
async def ai_brief_new():
    try:
        return await ai.brief()
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.post("/api/ai/test")
async def ai_test(body: dict = Body(default={})):
    target = body.get("target", "primary")
    st = load_settings()["ai"]
    spec = st["fallback"] if target == "fallback" else st["primary"]
    t0 = time.time()
    text, err = "", None
    try:
        async for ev in ai._stream_spec(spec, [{"role": "user", "content": "Reply with exactly: ROOKERY_OK"}], st):
            if ev["type"] == "delta":
                text += ev["text"]
    except Exception as e:  # noqa: BLE001
        err = str(e)
    return {"ok": "ROOKERY_OK" in text, "reply": text[:200], "error": err, "ms": round((time.time() - t0) * 1000),
            "model": ai._label(spec)}


# ------------------------------------------------------------------ websocket
@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    HUB.clients.add(sock)
    try:
        await sock.send_text(orjson.dumps({"type": "hello", "liq": crypto.LIQS.summary()}).decode())
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        HUB.clients.discard(sock)


# ------------------------------------------------------------------ SPA
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    f = FRONTEND_DIST / path
    if path and f.is_file():
        return FileResponse(f)
    idx = FRONTEND_DIST / "index.html"
    if idx.exists():
        return FileResponse(idx, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "frontend not built: run scripts/build.sh"}, status_code=503)
