"""Setup detectors (vectorised, no look-ahead), honest historical stats per setup, pivot support/resistance.

Every detector at bar i only uses bars <= i. Stats count *onsets* (first bar of a condition, with a cooldown)
and compare the forward hit rate with the unconditional base rate over the same history. Few events or a gap
inside the noise band are reported as "too few" / "no clear edge" -- that is the common, truthful answer.
"""
import math

import numpy as np

from . import indicators as ind

HORIZON = {"1h": 24, "4h": 6, "1d": 7}           # forward bars used for stats
HORIZON_LABEL = {"1h": "24h", "4h": "24h", "1d": "7d"}
COOLDOWN = {"1h": 12, "4h": 6, "1d": 5}          # min bars between two onsets of the same setup
LOOKBACK = {"1h": 6, "4h": 3, "1d": 2}           # an onset this recent counts as "active"
IV_SEC = {"1h": 3600, "4h": 14400, "1d": 86400}

SETUPS = {
    "pullback_up": ("Pullback in uptrend", "bull",
                    "Price dipped to the EMA21 inside an uptrend (above EMA50 and EMA200) and held."),
    "pullback_down": ("Rejected bounce in downtrend", "bear",
                      "Price bounced into the EMA21 inside a downtrend and was rejected: rallies are being sold."),
    "breakout": ("Breakout on volume", "bull", "Closed above the prior 50-bar high on 1.5x+ normal volume."),
    "breakdown": ("Breakdown on volume", "bear", "Closed below the prior 50-bar low on 1.5x+ normal volume."),
    "squeeze": ("Volatility squeeze", "neutral",
                "Bollinger bands are the tightest in ~180 bars. A big move often follows; direction unknown."),
    "bull_div": ("Bullish RSI divergence", "bull",
                 "Price made a lower low but RSI made a higher low: selling pressure is fading."),
    "bear_div": ("Bearish RSI divergence", "bear",
                 "Price made a higher high but RSI made a lower high: buying pressure is fading."),
    "golden_cross": ("Golden cross", "bull", "EMA50 crossed above EMA200: the slow trend is turning up (lagging)."),
    "death_cross": ("Death cross", "bear", "EMA50 crossed below EMA200: the slow trend is turning down (lagging)."),
    "ema_up": ("EMA9/21 bullish cross", "bull", "Short-term momentum flipped up while price is above EMA200."),
    "ema_down": ("EMA9/21 bearish cross", "bear", "Short-term momentum flipped down while price is below EMA200."),
    "oversold": ("Deeply oversold", "bull",
                 "RSI under 25: stretched down. Bounces are common, but trends can stay oversold."),
    "overbought": ("Very overbought", "bear",
                   "RSI above 78: stretched up. Pullbacks are common, but strong trends can stay overbought."),
}
STATEFUL = {"squeeze", "oversold", "overbought"}  # also reported while the condition simply holds


def closed(rows, tf, now):
    iv = IV_SEC.get(tf, 3600)
    return [r for r in rows if r["t"] + iv <= now]


def _arr(rows, k):
    return np.array([r[k] for r in rows], dtype=float)


def _prev(x):
    out = np.empty_like(x)
    out[0] = np.nan
    out[1:] = x[:-1]
    return out


def _pivots(x, k, kind):
    """Indices j where x[j] is the max (kind='hi') / min ('lo') of x[j-k : j+k+1]. Confirmed at bar j+k."""
    n = len(x)
    if n < 2 * k + 1:
        return np.array([], dtype=int)
    w = np.lib.stride_tricks.sliding_window_view(x, 2 * k + 1)
    centre = x[k:n - k]
    m = w.max(axis=1) if kind == "hi" else w.min(axis=1)
    return np.where(centre == m)[0] + k


def detect(rows):
    """{setup: bool array} -- condition true at bar i using data up to i only."""
    n = len(rows)
    out = {k: np.zeros(n, dtype=bool) for k in SETUPS}
    if n < 60:
        return out
    c, h, lo, v = _arr(rows, "c"), _arr(rows, "h"), _arr(rows, "l"), _arr(rows, "v")
    e9, e21, e50, e200 = (ind.ema(c, k) for k in (9, 21, 50, 200))
    r = ind.rsi(c, 14)
    with np.errstate(invalid="ignore"):
        up = (c > e50) & (e50 > e200)
        dn = (c < e50) & (e50 < e200)
        out["pullback_up"] = up & (lo <= e21 * 1.002) & (c > e21) & (r >= 38) & (r <= 60)
        out["pullback_down"] = dn & (h >= e21 * 0.998) & (c < e21) & (r >= 40) & (r <= 62)
        prev_hi = _prev(ind.rolling_max(h, 50))
        prev_lo = _prev(ind.rolling_min(lo, 50))
        vavg = _prev(ind.sma(v, 50))
        loud = v > 1.5 * vavg
        out["breakout"] = (c > prev_hi) & loud
        out["breakdown"] = (c < prev_lo) & loud
        blo, bmid, bhi = ind.bollinger(c, 20, 2)
        bw = (bhi - blo) / bmid
        if n >= 200:
            win = 180
            q = np.full(n, np.nan)
            sw = np.lib.stride_tricks.sliding_window_view(bw, win)
            q[win - 1:] = np.nanpercentile(sw, 10, axis=1)
            out["squeeze"] = bw <= q
        out["golden_cross"] = (e50 > e200) & (_prev(e50) <= _prev(e200))
        out["death_cross"] = (e50 < e200) & (_prev(e50) >= _prev(e200))
        out["ema_up"] = (e9 > e21) & (_prev(e9) <= _prev(e21)) & (c > e200)
        out["ema_down"] = (e9 < e21) & (_prev(e9) >= _prev(e21)) & (c < e200)
        out["oversold"] = r < 25
        out["overbought"] = r > 78
    # divergences: compare the last two confirmed pivots; the signal fires on the confirmation bar (no look-ahead)
    k = 3
    for kind, key in (("lo", "bull_div"), ("hi", "bear_div")):
        series = lo if kind == "lo" else h
        piv = _pivots(series, k, kind)
        for a, b in zip(piv[:-1], piv[1:]):
            if not 4 <= b - a <= 40 or np.isnan(r[a]) or np.isnan(r[b]):
                continue
            conf = b + k
            if conf >= n:
                continue
            if kind == "lo" and series[b] < series[a] and r[b] > r[a] + 2 and r[b] < 40:
                out[key][conf] = True
            if kind == "hi" and series[b] > series[a] and r[b] < r[a] - 2 and r[b] > 60:
                out[key][conf] = True
    return out


def onsets(cond, cooldown):
    """First bar of each run of True, at most one per `cooldown` bars."""
    idx = np.where(cond & ~np.concatenate(([False], cond[:-1])))[0]
    keep, last = [], -10 ** 9
    for i in idx:
        if i - last >= cooldown:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


def _verdict(n, edge, se, direction="bull"):
    if n < 15:
        return "too few"
    if abs(edge) < max(0.05, 1.5 * se):
        return "no clear edge"
    if direction == "neutral":  # hit = a bigger-than-usual move, either way
        return "big moves more likely" if edge > 0 else "quiet tends to persist"
    return "historically favorable" if edge > 0 else "historically unfavorable"


def backtest(rows, tf):
    """{setup: {n, hit, base, edge, avg, med, verdict, horizon}} over the supplied (closed) history."""
    H = HORIZON[tf]
    n = len(rows)
    if n < 250:
        return {}
    c = _arr(rows, "c")
    fwd = np.full(n, np.nan)
    fwd[:-H] = c[H:] / c[:-H] - 1
    valid = ~np.isnan(fwd)
    base_up = float(np.mean(fwd[valid] > 0))
    med_abs = float(np.median(np.abs(fwd[valid])))
    conds = detect(rows)
    out = {}
    for key, (label, d, _) in SETUPS.items():
        ev = onsets(conds[key], COOLDOWN[tf])
        ev = ev[ev < n - H]
        if not len(ev):
            out[key] = {"n": 0, "verdict": "too few", "horizon": HORIZON_LABEL[tf]}
            continue
        r = fwd[ev]
        if d == "bull":
            hits, base = r > 0, base_up
        elif d == "bear":
            hits, base = r < 0, 1 - base_up
        else:
            hits, base = np.abs(r) > med_abs, 0.5
        p = float(hits.mean())
        se = math.sqrt(max(p * (1 - p), 1e-6) / len(ev))
        out[key] = {"n": int(len(ev)), "hit": p, "base": base, "edge": p - base, "avg": float(r.mean()),
                    "med": float(np.median(r)), "verdict": _verdict(len(ev), p - base, se, d),
                    "horizon": HORIZON_LABEL[tf], "since": rows[0]["t"]}
    return out


def active(rows, tf):
    """Setups that fired recently (or, for state setups, still hold) on the last closed bar."""
    n = len(rows)
    if n < 60:
        return []
    conds = detect(rows)
    out = []
    for key, (label, d, explain) in SETUPS.items():
        cond = conds[key]
        ev = onsets(cond, COOLDOWN[tf])
        age = (n - 1 - int(ev[-1])) if len(ev) else None
        holding = bool(cond[-1]) and key in STATEFUL
        if (age is not None and age < LOOKBACK[tf]) or holding:
            out.append({"id": key, "label": label, "dir": d, "tf": tf, "explain": explain,
                        "age_bars": age if age is not None else 0, "holding": holding,
                        "t": rows[-1 - (age or 0)]["t"], "price": rows[-1]["c"]})
    return out


def levels(rows, k=3, lookback=180):
    """Support / resistance from clustered pivot highs+lows (tolerance 0.5 ATR). Strength = touches."""
    rows = rows[-lookback:]
    if len(rows) < 30:
        return {"support": [], "resistance": []}
    c, h, lo = _arr(rows, "c"), _arr(rows, "h"), _arr(rows, "l")
    a = ind.last(ind.atr(h, lo, c, 14)) or float(np.std(c) * 0.1)
    pts = [(float(h[j]), int(rows[j]["t"])) for j in _pivots(h, k, "hi")]
    pts += [(float(lo[j]), int(rows[j]["t"])) for j in _pivots(lo, k, "lo")]
    pts.sort()
    clusters: list[dict] = []
    for p, t in pts:
        if clusters and p - clusters[-1]["hi"] <= 0.5 * a:
            cl = clusters[-1]
            cl["prices"].append(p)
            cl["hi"] = p
            cl["last"] = max(cl["last"], t)
        else:
            clusters.append({"prices": [p], "hi": p, "last": t})
    price = float(c[-1])
    sup, res = [], []
    for cl in clusters:
        lvl = float(np.mean(cl["prices"]))
        e = {"price": lvl, "dist_pct": (lvl / price - 1) * 100, "touches": len(cl["prices"]), "last_t": cl["last"]}
        (sup if lvl < price else res).append(e)
    sup.sort(key=lambda e: -e["price"])
    res.sort(key=lambda e: e["price"])
    return {"support": sup[:3], "resistance": res[:3], "atr": a}
