"""Condition engine (Elfa-Auto style): AND/OR conditions over price, TA, funding, sentiment, model probability."""
import asyncio
import json
import shutil
import subprocess
import time

from . import db, predict, signals
from .config import load_settings
from .sources import crypto, sentiment

_prev: dict[str, float] = {}


def list_alerts():
    rows = db.q("SELECT * FROM alerts ORDER BY id DESC")
    for r in rows:
        r["spec"] = json.loads(r["spec"])
    return rows


def create(spec):
    return db.x("INSERT INTO alerts(name, spec, enabled, cooldown_min, created) VALUES (?,?,?,?,?)",
                (spec.get("name") or "Alert", json.dumps(spec), 1, int(spec.get("cooldown_min") or 60), time.time()))


def update(aid, enabled=None, spec=None):
    if enabled is not None:
        db.x("UPDATE alerts SET enabled=? WHERE id=?", (1 if enabled else 0, aid))
    if spec is not None:
        db.x("UPDATE alerts SET spec=?, name=?, cooldown_min=? WHERE id=?",
             (json.dumps(spec), spec.get("name") or "Alert", int(spec.get("cooldown_min") or 60), aid))


def delete(aid):
    db.x("DELETE FROM alerts WHERE id=?", (aid,))


def events(limit=100):
    rows = db.q("SELECT e.*, a.name FROM alert_events e LEFT JOIN alerts a ON a.id = e.alert_id ORDER BY e.id DESC"
                " LIMIT ?", (limit,))
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}")
    return rows


async def _metric(c, cache):
    sym, metric, kind = (c.get("sym") or "").upper(), c["metric"], c.get("kind") or "crypto"
    if metric == "fng":
        return (await sentiment.crypto_fear_greed())["value"]
    if metric == "liq_1h":
        s = crypto.LIQS.summary()
        return s["total"]["long"] + s["total"]["short"]
    key = (sym, kind)
    if key not in cache:
        cache[key] = await (signals.crypto_signal(sym, with_pos=metric == "oi_chg24") if kind == "crypto"
                            else signals.stock_signal(sym))
    sig = cache[key]
    t1, td = sig.get("t1h") or {}, sig.get("t1d") or {}
    s = sig.get("sentiment") or {}
    if metric == "price":
        return t1.get("price")
    if metric == "chg24":
        if kind == "crypto":
            t = await crypto.tickers([sym])
            return t[0]["chg24"] if t else None
        return None
    if metric == "rsi_1h":
        return t1.get("rsi")
    if metric == "rsi_1d":
        return td.get("rsi")
    if metric == "dist_ema200_1d":
        return (td["price"] / td["ema200"] - 1) * 100 if td.get("ema200") else None
    if metric == "ema9_21_1h":
        return (t1["ema9"] - t1["ema21"]) if t1.get("ema9") and t1.get("ema21") else None
    if metric == "funding":
        return (sig.get("deriv") or {}).get("fund8h")
    if metric == "oi_chg24":
        return (sig.get("positioning") or {}).get("oi_chg24")
    if metric == "sentiment":
        return s.get("sentiment", 0.0)
    if metric == "mentions":
        return s.get("mentions", 0)
    if metric == "velocity":
        return s.get("velocity", 0.0)
    if metric == "score":
        return sig.get("score")
    if metric == "p_up_24":
        cur = [p for p in predict.current() if p["sym"] == sym and p["h"] == 24]
        return cur[0]["p_up"] if cur else None
    return None


def _check(c, v, key):
    if v is None:
        return False
    op, th = c["op"], float(c.get("value") or 0)
    prev = _prev.get(key)
    _prev[key] = v
    if op == ">":
        return v > th
    if op == "<":
        return v < th
    if op == "crosses_above":
        return prev is not None and prev <= th < v
    if op == "crosses_below":
        return prev is not None and prev >= th > v
    return False


def notify_desktop(title, body):
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.Popen(["notify-send", "-a", "Rook Market Analyser", "-i", "office-chart-line", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


async def evaluate(broadcast):
    st = load_settings()
    cache = {}
    fired = []
    for a in db.q("SELECT * FROM alerts WHERE enabled = 1"):
        spec = json.loads(a["spec"])
        conds = spec.get("conditions") or []
        if not conds:
            continue
        results, vals = [], []
        for i, c in enumerate(conds):
            try:
                v = await _metric(c, cache)
            except Exception:  # noqa: BLE001
                v = None
            vals.append({"sym": c.get("sym"), "metric": c["metric"], "op": c["op"], "value": c.get("value"), "now": v})
            results.append(_check(c, v, f"{a['id']}:{i}"))
        ok = all(results) if spec.get("logic", "all") == "all" else any(results)
        if not ok:
            continue
        if a["last_fired"] and time.time() - a["last_fired"] < (a["cooldown_min"] or 60) * 60:
            continue
        msg = spec.get("message") or " & ".join(
            f"{v['sym'] or ''} {v['metric']} {v['op']} {v['value']} (now {v['now']:.4g})" for v in vals
            if v["now"] is not None)
        db.x("UPDATE alerts SET last_fired=? WHERE id=?", (time.time(), a["id"]))
        eid = db.x("INSERT INTO alert_events(alert_id, ts, message, data) VALUES (?,?,?,?)",
                   (a["id"], time.time(), msg, json.dumps({"values": vals})))
        ev = {"type": "alert", "id": eid, "alert_id": a["id"], "name": a["name"], "message": msg, "ts": time.time()}
        fired.append(ev)
        await broadcast(ev)
        if st["alerts"].get("desktop_notify", True):
            notify_desktop(f"Rook · {a['name']}", msg)
        if spec.get("ai_analysis"):
            asyncio.create_task(_ai_followup(eid, a["name"], msg, [c.get("sym") for c in conds if c.get("sym")],
                                             broadcast))
    return fired


async def _ai_followup(eid, name, msg, syms, broadcast):
    from . import ai
    try:
        ctx, _ = await ai.build_context(" ".join(syms), [s for s in syms if s and s != "MARKET"])
        text, model = await ai.complete([
            {"role": "system", "content": ai.system_prompt() + "\n\n" + ai.FAST_HINT + "\n\n" + ctx},
            {"role": "user", "content": f"Alert '{name}' just fired: {msg}. In under 150 words: what likely triggered it, "
                                        "is it meaningful or noise, and what to watch next."}])
        row = db.q("SELECT data FROM alert_events WHERE id=?", (eid,))
        data = json.loads(row[0]["data"]) if row else {}
        data["ai"] = {"text": text, "model": model}
        db.x("UPDATE alert_events SET data=? WHERE id=?", (json.dumps(data), eid))
        await broadcast({"type": "alert_ai", "id": eid, "text": text, "model": model})
    except Exception:  # noqa: BLE001
        pass
