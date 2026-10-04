"""Rook Score + setups + plain-language readout per asset.

Score: explainable composite of daily / 4h / 1h trend (EMA), momentum, sentiment and positioning, -100..100.
Setups: see setups.py (each comes with its own historical hit rate vs base rate).
Readout: the same evidence written as short sentences with a tone, for people who don't want to read tables.
Tracking: every setup onset on the watchlist is logged and graded later, so the live record accumulates.
"""
import asyncio
import json
import math
import time

import numpy as np

from . import db, setups
from . import indicators as ind
from .config import MODEL_DIR, load_settings
from .net import cached
from .sources import crypto, sentiment, stocks


def _arr(rows, k):
    return np.array([r[k] for r in rows], dtype=float)


def technicals(rows):
    """Indicator snapshot for one timeframe."""
    if not rows or len(rows) < 30:
        return None
    c, h, l, v = _arr(rows, "c"), _arr(rows, "h"), _arr(rows, "l"), _arr(rows, "v")
    e = {n: ind.ema(c, n) for n in (9, 21, 50, 200)}
    m, s, hist = ind.macd(c)
    r = ind.rsi(c)
    a = ind.atr(h, l, c)
    lo, mid, hi = ind.bollinger(c)
    price = float(c[-1])
    out = {"price": price, "rsi": ind.last(r), "macd": ind.last(m), "macd_signal": ind.last(s),
           "macd_hist": ind.last(hist), "atr": ind.last(a), "atr_pct": (ind.last(a) or 0) / price * 100,
           "bb_lo": ind.last(lo), "bb_hi": ind.last(hi), "bb_mid": ind.last(mid)}
    rv = r[~np.isnan(r)]
    out["rsi_prev"] = float(rv[-4]) if len(rv) > 4 else None  # 3 bars ago: is momentum rising or fading?
    for n, arr in e.items():
        out[f"ema{n}"] = ind.last(arr)
    out["cross_9_21"] = ind.cross(e[9], e[21], 3)
    out["cross_50_200"] = ind.cross(e[50], e[200], 5)
    vavg = float(np.mean(v[-50:-1])) if len(v) > 51 else None
    out["vol_ratio"] = float(v[-1] / vavg) if vavg else None
    look = min(len(rows), 50)
    out["swing_hi"] = float(h[-look:].max())
    out["swing_lo"] = float(l[-look:].min())
    out["chg_24b"] = float(c[-1] / c[-25] - 1) * 100 if len(c) > 25 else None
    stack = [out.get(f"ema{n}") for n in (9, 21, 50, 200)]
    if all(x is not None for x in stack):
        if stack[0] > stack[1] > stack[2] > stack[3]:
            out["ema_stack"] = "bull"
        elif stack[0] < stack[1] < stack[2] < stack[3]:
            out["ema_stack"] = "bear"
        else:
            out["ema_stack"] = "mixed"
    return out


def _sgn(a, b):
    if a is None or b is None:
        return 0.0
    return 1.0 if a > b else -1.0


WEIGHTS = {"trend_d": 0.25, "trend_4h": 0.15, "trend_h": 0.15, "momentum": 0.15, "sentiment": 0.15,
           "positioning": 0.15}


def score(t1h, t1d, sent=None, deriv=None, pos=None, t4h=None, xatt=None):
    comp, notes = {}, []
    if t1d:
        p = t1d["price"]
        comp["trend_d"] = np.mean([_sgn(p, t1d.get("ema21")), _sgn(p, t1d.get("ema50")), _sgn(p, t1d.get("ema200")),
                                   _sgn(t1d.get("ema50"), t1d.get("ema200"))])
        if t1d.get("cross_50_200") == 1:
            notes.append("Daily golden cross (EMA50 > EMA200) in the last 5 sessions")
        elif t1d.get("cross_50_200") == -1:
            notes.append("Daily death cross (EMA50 < EMA200) in the last 5 sessions")
        if t1d.get("ema200") and p < t1d["ema200"]:
            notes.append("Price below daily EMA200: long-term trend down")
    if t4h:
        p = t4h["price"]
        comp["trend_4h"] = np.mean([_sgn(t4h.get("ema9"), t4h.get("ema21")), _sgn(p, t4h.get("ema50")),
                                    _sgn(t4h.get("ema50"), t4h.get("ema200"))])
    if t1h:
        p = t1h["price"]
        comp["trend_h"] = np.mean([_sgn(t1h.get("ema9"), t1h.get("ema21")), _sgn(p, t1h.get("ema50")),
                                   _sgn(p, t1h.get("ema200"))])
        r = t1h.get("rsi") or 50
        mh = t1h.get("macd_hist") or 0
        comp["momentum"] = float(np.clip((r - 50) / 25, -1, 1) * 0.6 + (0.4 if mh > 0 else -0.4))
        if r >= 70:
            notes.append(f"1h RSI {r:.0f}: overbought/extended")
        elif r <= 30:
            notes.append(f"1h RSI {r:.0f}: oversold")
        if t1h.get("cross_9_21") == 1:
            notes.append("1h EMA9 crossed above EMA21")
        elif t1h.get("cross_9_21") == -1:
            notes.append("1h EMA9 crossed below EMA21")
        if (t1h.get("vol_ratio") or 0) > 3:
            notes.append(f"Volume spike: {t1h['vol_ratio']:.1f}x the 50-bar average")
    if sent and sent.get("mentions"):
        conf = min(sent["mentions"] / 10, 1.0)
        comp["sentiment"] = float(np.clip(sent["sentiment"] * 2.5, -1, 1) * conf)
        if sent.get("velocity", 0) >= 2 and sent["mentions"] >= 5:
            notes.append(f"News mention velocity {sent['velocity']:.1f}x: attention rising")
    if xatt and xatt.get("listed") and abs(xatt.get("chg") or 0) >= 50:
        notes.append(f"X mentions {xatt['chg']:+.0f}% vs prior 24h (Elfa rank #{xatt['rank']}): attention "
                     f"{'surging' if xatt['chg'] > 0 else 'fading'}")
    if deriv and deriv.get("fund8h") is not None:
        f = deriv["fund8h"]
        # contrarian: crowded longs (high positive funding) are fragile; deeply negative funding = squeeze fuel
        comp["positioning"] = float(-math.tanh(f / 0.03) * 0.7)
        if pos and pos.get("ls_retail"):
            ls = pos["ls_retail"][-1]["v"]
            comp["positioning"] += float(-math.tanh((ls - 1.0) / 1.0) * 0.3)
            if ls > 2.2:
                notes.append(f"Retail long/short {ls:.2f}: crowded long")
            elif ls < 0.8:
                notes.append(f"Retail long/short {ls:.2f}: crowded short")
        if f > 0.03:
            notes.append(f"Funding {f:+.4f}%/8h: longs paying up")
        elif f < -0.01:
            notes.append(f"Funding {f:+.4f}%/8h: shorts paying (squeeze fuel, not a long signal alone)")
    tot = sum(WEIGHTS[k] for k in comp)
    composite = sum(comp[k] * WEIGHTS[k] for k in comp) / tot * 100 if tot else 0.0
    label = ("Strong bullish" if composite >= 50 else "Bullish" if composite >= 18 else
             "Strong bearish" if composite <= -50 else "Bearish" if composite <= -18 else "Neutral")
    return {"score": round(float(composite), 1), "label": label, "components": {k: round(float(v), 3) for k, v in comp.items()},
            "notes": notes}


def _positioning_extras(pos, t1h):
    """Funding z-score vs its own 30-day history and OI/price divergence."""
    out, notes = {}, []
    f = [x["v"] for x in (pos or {}).get("funding") or []]
    if len(f) >= 30:
        mu, sd = float(np.mean(f[:-1])), float(np.std(f[:-1]))
        if sd > 0:
            out["funding_z"] = (f[-1] - mu) / sd
            if out["funding_z"] > 2:
                notes.append(f"Funding {out['funding_z']:.1f} std above its 30-day norm: longs unusually crowded")
            elif out["funding_z"] < -2:
                notes.append(f"Funding {abs(out['funding_z']):.1f} std below its 30-day norm: shorts unusually crowded")
    oi, pc = (pos or {}).get("oi_chg24"), (t1h or {}).get("chg_24b")
    if oi is not None and pc is not None:
        out["oi_chg24"], out["px_chg24"] = oi, pc
        if pc > 2 and oi < -4:
            out["oi_div"] = "short_covering"
            notes.append(f"Price +{pc:.1f}% while OI {oi:.1f}%: rally driven by shorts closing (less conviction)")
        elif pc < -2 and oi > 4:
            out["oi_div"] = "shorts_pressing"
            notes.append(f"Price {pc:.1f}% while OI +{oi:.1f}%: new shorts pressing (watch for a squeeze if it stalls)")
        elif pc > 2 and oi > 4:
            out["oi_div"] = "levered_longs"
            notes.append(f"Price +{pc:.1f}% with OI +{oi:.1f}%: new leverage behind the move (fuel and liquidation risk)")
    return out, notes


# ------------------------------------------------------------------ setup stats (cached, run in a thread)
async def _deep_rows(sym, kind, tf):
    if kind == "crypto" and tf == "1h":
        f = MODEL_DIR / f"hist_{sym}.json"
        if f.exists():  # the forecast models keep ~2 years of 1h bars on disk
            try:
                return json.loads(f.read_text())
            except json.JSONDecodeError:
                pass
    if kind == "crypto":
        return (await crypto.klines(sym, tf, 1000))["rows"]
    if kind == "dex":
        from .sources import solana
        return (await solana.dex_klines(sym, tf, 1000))["rows"]
    iv = "1h" if tf == "4h" else tf
    rows = (await stocks.klines(sym, iv))["rows"]
    return stocks._resample(rows, 14400) if tf == "4h" else rows


async def setup_stats(sym, kind="crypto"):
    async def fetch():
        now = time.time()
        out = {}
        for tf in ("1h", "4h", "1d"):
            try:
                rows = setups.closed(await _deep_rows(sym, kind, tf), tf, now)
                out[tf] = await asyncio.to_thread(setups.backtest, rows, tf)
                out[tf + "_bars"] = len(rows)
            except Exception:  # noqa: BLE001
                out[tf] = {}
        return out
    return await cached(f"setupstats:{kind}:{sym}", 6 * 3600, fetch)


def _attach_stats(active, stats):
    for a in active:
        a["stats"] = (stats.get(a["tf"]) or {}).get(a["id"])
    return active


async def _active_setups(rows_by_tf, sym, kind, with_stats):
    now = time.time()
    act = []
    for tf, rows in rows_by_tf.items():
        if rows:
            act += setups.active(setups.closed(rows, tf, now), tf)
    if with_stats and act:
        try:
            _attach_stats(act, await setup_stats(sym, kind))
        except Exception:  # noqa: BLE001
            pass
    order = {"1d": 0, "4h": 1, "1h": 2}
    act.sort(key=lambda a: (order[a["tf"]], a["age_bars"]))
    return act


def _levels(r4h, r1d, now):
    near = setups.levels(setups.closed(r4h or [], "4h", now)) if r4h else {"support": [], "resistance": []}
    major = setups.levels(setups.closed(r1d or [], "1d", now)) if r1d else {"support": [], "resistance": []}
    return {"near": near, "major": major}


# ------------------------------------------------------------------ assets
async def crypto_signal(sym, with_pos=False, deep=True):
    from .sources import elfa
    sym = sym.upper()
    res = await asyncio.gather(crypto.klines(sym, "1h", 300), crypto.klines(sym, "4h", 300),
                               crypto.klines(sym, "1d", 300), crypto.derivs_universe(), return_exceptions=True)
    k1h, k4h, k1d, dv = [None if isinstance(r, BaseException) else r for r in res]
    r1h, r4h, r1d = (k["rows"] if k else None for k in (k1h, k4h, k1d))
    t1h, t4h, t1d = technicals(r1h), technicals(r4h), technicals(r1d)
    deriv = next((r for r in (dv or {}).get("rows", []) if r["sym"] == sym), None) if dv else None
    agg = sentiment.aggregate()
    sent = next((r for r in agg["rows"] if r["sym"] == sym), None)
    xatt = elfa.x_attention(sym)
    pos = None
    if with_pos and deriv:
        try:
            pos = await crypto.positioning(sym)
        except Exception:  # noqa: BLE001
            pos = None
    sc = score(t1h, t1d, sent, deriv, pos, t4h, xatt)
    extras, extra_notes = _positioning_extras(pos, t1h) if pos else ({}, [])
    sc["notes"] += extra_notes
    now = time.time()
    act = await _active_setups({"1h": r1h, "4h": r4h, "1d": r1d}, sym, "crypto", deep)
    out = {"sym": sym, "kind": "crypto", "t1h": t1h, "t4h": t4h, "t1d": t1d, "deriv": deriv, "sentiment": sent,
           "x": xatt, "positioning": {"oi_chg24": (pos or {}).get("oi_chg24"), **extras} if pos else None,
           "setups": act, "levels": _levels(r4h, r1d, now) if deep else None, **sc}
    from . import predict
    out["readout"] = readout(out, [p for p in predict.current() if p["sym"] == sym])
    return out


async def stock_signal(sym, deep=True):
    res = await asyncio.gather(stocks.klines(sym, "1h"), stocks.klines(sym, "1d"), return_exceptions=True)
    k1h, k1d = [None if isinstance(r, BaseException) else r for r in res]
    r1h, r1d = (k["rows"] if k else None for k in (k1h, k1d))
    r4h = stocks._resample(r1h, 14400) if r1h else None
    t1h, t4h, t1d = technicals(r1h), technicals(r4h), technicals(r1d)
    agg = sentiment.aggregate()
    sent = next((r for r in agg["rows"] if r["sym"] == sym), None)
    sc = score(t1h, t1d, sent, t4h=t4h)
    act = await _active_setups({"1h": r1h, "4h": r4h, "1d": r1d}, sym, "stock", deep)
    out = {"sym": sym, "kind": "stock", "t1h": t1h, "t4h": t4h, "t1d": t1d, "sentiment": sent, "setups": act,
           "levels": _levels(r4h, r1d, time.time()) if deep else None, **sc}
    out["readout"] = readout(out)
    return out


async def dex_signal(mint):
    """On-chain-only token: technicals/setups from GeckoTerminal candles of its deepest pool; no derivatives."""
    from .sources import solana
    res = await asyncio.gather(solana.dex_klines(mint, "1h", 500), solana.dex_klines(mint, "4h", 500),
                               solana.dex_klines(mint, "1d", 300), solana.token_info([mint]), return_exceptions=True)
    k1h, k4h, k1d, info = [None if isinstance(r, BaseException) else r for r in res]
    r1h, r4h, r1d = (k["rows"] if k else None for k in (k1h, k4h, k1d))
    t1h, t4h, t1d = technicals(r1h), technicals(r4h), technicals(r1d)
    i = (info or {}).get(mint) or {}
    sym = (i.get("symbol") or mint[:6]).upper()
    agg = sentiment.aggregate()
    sent = next((r for r in agg["rows"] if r["sym"] == sym), None)
    sc = score(t1h, t1d, sent, t4h=t4h)
    if (i.get("liquidity") or 0) < 250_000:
        sc["notes"].append(f"Thin liquidity (${(i.get('liquidity') or 0):,.0f}): slippage and rug risk are real")
    if not i.get("verified"):
        sc["notes"].append("Not on Jupiter's verified list: treat as high risk")
    act = await _active_setups({"1h": r1h, "4h": r4h, "1d": r1d}, mint, "dex", True)
    out = {"sym": sym, "mint": mint, "kind": "dex", "t1h": t1h, "t4h": t4h, "t1d": t1d, "sentiment": sent,
           "token": i, "setups": act, "levels": _levels(r4h, r1d, time.time()), **sc}
    out["readout"] = readout(out)
    return out


async def scan(cryptos, stock_syms, deep=False):
    tasks = [crypto_signal(s, deep=deep) for s in cryptos] + [stock_signal(s, deep=deep) for s in stock_syms]
    res = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in res if not isinstance(r, BaseException)]


# ------------------------------------------------------------------ plain-language readout
def _money(x):
    if x is None:
        return "n/a"
    if x >= 1000:
        return f"${x:,.0f}"
    if x >= 1:
        return f"${x:,.2f}"
    return f"${x:.6g}"


def _rsi_words(r):
    if r is None:
        return "unknown"
    if r >= 70:
        return "overbought"
    if r >= 58:
        return "strong"
    if r > 42:
        return "neutral"
    if r > 30:
        return "weak"
    return "oversold"


def readout(sig, preds=None):
    """[{k, tone, text}] + bottom line. Deterministic: same data -> same words."""
    lines = []
    t1h, t4h, td = sig.get("t1h") or {}, sig.get("t4h") or {}, sig.get("t1d") or {}

    if td.get("ema200"):
        p = td["price"]
        above50, above200 = p > (td.get("ema50") or 0), p > td["ema200"]
        if above50 and above200:
            lines.append({"k": "Trend", "tone": "bull", "text": "Long-term uptrend: price is above the daily EMA50 "
                                                                "and EMA200."})
        elif not above50 and not above200:
            lines.append({"k": "Trend", "tone": "bear", "text": "Long-term downtrend: price is below the daily EMA50 "
                                                                "and EMA200."})
        else:
            lines.append({"k": "Trend", "tone": "neutral", "text": "Mixed long-term trend: price sits between the "
                                                                   "daily EMA50 and EMA200."})
    if t4h.get("ema_stack") or t1h.get("ema_stack"):
        s4, s1 = t4h.get("ema_stack"), t1h.get("ema_stack")
        words = {"bull": "up", "bear": "down", "mixed": "choppy", None: "unknown"}
        tone = "bull" if s4 == "bull" and s1 != "bear" else "bear" if s4 == "bear" and s1 != "bull" else "neutral"
        lines.append({"k": "Short term", "tone": tone, "text": f"4h trend {words[s4]}, 1h {words[s1]}."})
    r1, rd = t1h.get("rsi"), td.get("rsi")
    if r1 is not None:
        trend = ""
        if t1h.get("rsi_prev") is not None:
            d = r1 - t1h["rsi_prev"]
            trend = ", rising" if d > 3 else ", fading" if d < -3 else ""
        tone = "bear" if (r1 or 50) >= 72 else "bull" if (r1 or 50) <= 30 else ("bull" if r1 > 55 else "bear" if r1 < 45 else "neutral")
        lines.append({"k": "Momentum", "tone": tone,
                      "text": f"1h momentum {_rsi_words(r1)} (RSI {r1:.0f}{trend}); daily {_rsi_words(rd)}"
                              + (f" (RSI {rd:.0f})." if rd is not None else ".")})
    lv = (sig.get("levels") or {}).get("near") or {}
    s, r = (lv.get("support") or [None])[0], (lv.get("resistance") or [None])[0]
    if s or r:
        parts = []
        if s:
            parts.append(f"support {_money(s['price'])} ({s['dist_pct']:+.1f}%)")
        if r:
            parts.append(f"resistance {_money(r['price'])} ({r['dist_pct']:+.1f}%)")
        lines.append({"k": "Levels", "tone": "neutral", "text": "Nearest " + ", ".join(parts) + "."})
    d, p = sig.get("deriv"), sig.get("positioning") or {}
    if d and d.get("fund8h") is not None:
        f = d["fund8h"]
        if f > 0.03 or (p.get("funding_z") or 0) > 2:
            txt, tone = f"Crowded longs: funding {f:+.4f}%/8h. Longs are paying; a flush lower is the risk.", "bear"
        elif f < -0.01 or (p.get("funding_z") or 0) < -2:
            txt, tone = f"Crowded shorts: funding {f:+.4f}%/8h. Squeeze fuel, but not a buy signal on its own.", "neutral"
        else:
            txt, tone = f"Leverage looks normal (funding {f:+.4f}%/8h).", "neutral"
        if p.get("oi_div") == "short_covering":
            txt += " The latest rally came from shorts closing, not new buyers."
        elif p.get("oi_div") == "levered_longs":
            txt += " New leverage is chasing the move."
        elif p.get("oi_div") == "shorts_pressing":
            txt += " Open interest is rising into the drop: shorts pressing."
        lines.append({"k": "Positioning", "tone": tone, "text": txt})
    se, x = sig.get("sentiment"), sig.get("x")
    soc = []
    tone = "neutral"
    if se and se.get("mentions"):
        t = se["sentiment"]
        word = "positive" if t >= 0.15 else "negative" if t <= -0.15 else "neutral"
        tone = "bull" if t >= 0.15 else "bear" if t <= -0.15 else "neutral"
        soc.append(f"news tone {word} ({t:+.2f} across {se['mentions']} mentions)")
    if x and x.get("listed"):
        soc.append(f"X mentions {x['chg']:+.0f}% vs the prior 24h (#{x['rank']} on Elfa)")
    if soc:
        lines.append({"k": "Crowd", "tone": tone, "text": soc[0][:1].upper() + soc[0][1:] + ("; " + soc[1] if len(soc) > 1 else "") + "."})
    for pr in (preds or []):
        if pr.get("h") == 24:
            pu = pr["p_up"]
            edge = pr.get("edge") or "untested"
            meaning = ("treat it as a coin flip" if edge in ("no edge", "too early", "untested")
                       else "small, unproven tilt" if edge.startswith("weak") else "the models have earned some trust here")
            lines.append({"k": "Models", "tone": "neutral" if edge in ("no edge", "too early", "untested") else
                          ("bull" if pu > 0.5 else "bear"),
                          "text": f"{pu * 100:.0f}% chance of being higher in 24h. Edge: {edge}, so {meaning}."})
    act = sig.get("setups") or []
    if act:
        a = act[0]
        st = a.get("stats") or {}
        hist = ""
        if st.get("n"):
            hist = (f" Historically {st['hit'] * 100:.0f}% {'up' if a['dir'] == 'bull' else 'down' if a['dir'] == 'bear' else 'big moves'}"
                    f" within {st['horizon']} (n={st['n']}, base {st['base'] * 100:.0f}%): {st['verdict']}.")
        more = f" (+{len(act) - 1} more)" if len(act) > 1 else ""
        lines.append({"k": "Setup", "tone": a["dir"] if a["dir"] != "neutral" else "neutral",
                      "text": f"{a['label']} on {a['tf']}{more}.{hist}"})
    bull = sum(1 for ln in lines if ln["tone"] == "bull")
    bear = sum(1 for ln in lines if ln["tone"] == "bear")
    risk = next((ln["text"].split(":")[0] for ln in lines if ln["k"] == "Positioning" and ln["tone"] == "bear"), None)
    label = sig.get("label") or "Neutral"
    agree = "signals mostly agree" if abs(bull - bear) >= 2 else "signals are mixed"
    bottom = f"{label} ({sig.get('score', 0):+.0f}/100): {agree} ({bull} bullish, {bear} bearish)."
    if risk:
        bottom += f" Main risk: {risk.lower()}."
    return {"lines": lines, "bottom_line": bottom}


# ------------------------------------------------------------------ live tracking of setup onsets
def track(sig):
    """Log setups whose onset is the last closed bar (deduped per sym/setup/tf over the cooldown)."""
    if not load_settings()["signals"].get("track", True):
        return 0
    n = 0
    for a in sig.get("setups") or []:
        if a["age_bars"] != 0:  # only the onset bar; "holding" states that started earlier were logged then
            continue
        iv = setups.IV_SEC[a["tf"]]
        if db.q("SELECT 1 FROM signal_events WHERE sym=? AND setup=? AND tf=? AND ts > ?",
                (sig["sym"], a["id"], a["tf"], a["t"] - setups.COOLDOWN[a["tf"]] * iv)):
            continue
        t0 = a["t"] + iv  # setup is known when its bar closes
        db.x("INSERT INTO signal_events(ts, sym, kind, setup, direction, tf, price, resolve_at) VALUES (?,?,?,?,?,?,?,?)",
             (t0, sig["sym"], sig["kind"], a["id"], a["dir"], a["tf"], a["price"], t0 + setups.HORIZON[a["tf"]] * iv))
        n += 1
    return n


async def grade_events():
    due = db.q("SELECT * FROM signal_events WHERE hit IS NULL AND price_end IS NULL AND resolve_at <= ? LIMIT 100",
               (time.time() - 300,))
    done = 0
    for e in due:
        try:
            if e["kind"] == "crypto":
                pe = await crypto.price_at(e["sym"], e["resolve_at"] // 1800 * 1800)
            else:
                rows = (await stocks.klines(e["sym"], "1h"))["rows"]
                after = [r for r in rows if r["t"] >= e["resolve_at"] - 3600]
                pe = after[0]["c"] if after else None
        except Exception:  # noqa: BLE001
            continue
        if not pe or not e["price"]:
            continue
        ret = pe / e["price"] - 1
        hit = None if e["direction"] == "neutral" else int(ret > 0) if e["direction"] == "bull" else int(ret < 0)
        db.x("UPDATE signal_events SET price_end=?, ret=?, hit=? WHERE id=?", (pe, ret, hit, e["id"]))
        done += 1
    return done


def live_record(days=180):
    rows = db.q("SELECT setup, tf, direction, hit, ret FROM signal_events WHERE price_end IS NOT NULL AND ts > ?",
                (time.time() - days * 86400,))
    agg: dict = {}
    for r in rows:
        a = agg.setdefault((r["setup"], r["tf"]), {"setup": r["setup"], "tf": r["tf"], "dir": r["direction"],
                                                   "label": setups.SETUPS.get(r["setup"], (r["setup"],))[0],
                                                   "n": 0, "hits": 0, "graded": 0, "ret_sum": 0.0})
        a["n"] += 1
        a["ret_sum"] += r["ret"] or 0
        if r["hit"] is not None:
            a["graded"] += 1
            a["hits"] += r["hit"]
    out = []
    for a in agg.values():
        out.append({**{k: a[k] for k in ("setup", "tf", "dir", "label", "n")},
                    "hit": a["hits"] / a["graded"] if a["graded"] else None, "avg_ret": a["ret_sum"] / a["n"]})
    out.sort(key=lambda r: -r["n"])
    pending = db.q("SELECT COUNT(*) n FROM signal_events WHERE price_end IS NULL")[0]["n"]
    recent = db.q("SELECT * FROM signal_events ORDER BY ts DESC LIMIT 40")
    for r in recent:
        r["label"] = setups.SETUPS.get(r["setup"], (r["setup"],))[0]
    return {"rows": out, "pending": pending, "recent": recent}
