"""Self-grading probabilistic forecasts.

Loop (per symbol x horizon):
  1. TRAIN   - deep 1h history -> features -> purged walk-forward CV of {base-rate, logistic, gradient boosting}.
               Out-of-sample Brier per model sets initial ensemble weights (Hedge) + a Platt calibration.
  2. ISSUE   - every hour on closed bars: P(close[t+h] > close[t]) from the calibrated ensemble, plus an
               online model that learns live-only features (sentiment, F&G, OI, L/S) from graded outcomes.
  3. GRADE   - when a forecast matures, price at resolve time from exchange history -> Brier vs base rate.
  4. ADAPT   - graded outcomes re-weight the ensemble (multiplicative weights), train the online model,
               and the scoreboard + LLM track record feed back into the AI prompt.
Skill is always reported against the base rate. "No edge" is a legitimate, common answer.
"""
import asyncio
import json
import math
import time
import warnings

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import db
from . import indicators as ind
from .config import MODEL_DIR
from .sources import crypto, sentiment

warnings.filterwarnings("ignore", category=UserWarning)

FEATURES = ["r1", "r4", "r24", "r72", "r168", "d_ema9", "d_ema21", "d_ema50", "d_ema200", "ema21_slope",
            "rsi14", "rsi56", "macd_h", "atr_pct", "rv24", "rv168", "rv_ratio", "bb_pos", "vol_z", "range_pos",
            "hour_sin", "hour_cos", "dow_sin", "dow_cos", "funding", "funding_avg3", "btc_r24", "btc_d_ema50",
            "tk4", "tk24"]
# live-only features (no deep history exists for them): learned online from graded outcomes
LIVE_EXTRA = ["sent", "sent_mentions", "fng", "oi_chg24", "ls_retail", "x_mentions", "x_chg", "x_listed"]
QUANTILES = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
EWMA_SPAN = 72  # hours: volatility memory for the price cone
_NORM_Z = {0.05: -1.6449, 0.10: -1.2816, 0.25: -0.6745, 0.50: 0.0, 0.75: 0.6745, 0.90: 1.2816, 0.95: 1.6449}
ONLINE_VERSION = 2
MODELS = ["base", "logit", "gbm", "online"]
STATE_FILE = MODEL_DIR / "state.json"
HIST_BARS = 17520  # ~2 years of 1h bars
ETA = 4.0          # learning rate of the live multiplicative-weights update
W_FLOOR = 0.03

_lock = asyncio.Lock()


# ------------------------------------------------------------------ state
def _load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(s):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE_FILE)


# ------------------------------------------------------------------ history cache
async def history(sym):
    """Deep 1h history cached on disk, topped up incrementally."""
    f = MODEL_DIR / f"hist_{sym}.json"
    rows = []
    if f.exists():
        try:
            rows = json.loads(f.read_text())
        except json.JSONDecodeError:
            rows = []
    # refetch fully when the cache predates taker-flow fields (Binance rows carry "tb")
    if len(rows) < HIST_BARS * 0.5 or not any("tb" in r for r in rows[-50:]):
        rows = await crypto.klines_history(sym, "1h", HIST_BARS)
    else:
        latest = await crypto.klines(sym, "1h", 1000)
        have = {r["t"] for r in rows}
        rows += [r for r in latest["rows"] if r["t"] not in have]
        by = {r["t"]: r for r in rows}
        for r in latest["rows"]:
            by[r["t"]] = r  # refresh the still-forming bars
        rows = sorted(by.values(), key=lambda r: r["t"])[-HIST_BARS:]
    f.write_text(json.dumps(rows))
    return rows


def closed(rows, now=None):
    now = now or time.time()
    return [r for r in rows if r["t"] + 3600 <= now]


# ------------------------------------------------------------------ features
def build_features(rows, funding=None, btc=None):
    t = np.array([r["t"] for r in rows], dtype=float)
    c = np.array([r["c"] for r in rows], dtype=float)
    h = np.array([r["h"] for r in rows], dtype=float)
    lo = np.array([r["l"] for r in rows], dtype=float)
    v = np.array([r["v"] for r in rows], dtype=float)
    lc = np.log(c)
    n = len(c)

    def lag(x, k):
        out = np.full(n, np.nan)
        out[k:] = x[:-k]
        return out

    F = {}
    for k in (1, 4, 24, 72, 168):
        F[f"r{k}"] = lc - lag(lc, k)
    for k in (9, 21, 50, 200):
        F[f"d_ema{k}"] = c / ind.ema(c, k) - 1
    e21 = ind.ema(c, 21)
    F["ema21_slope"] = np.log(e21) - lag(np.log(e21), 24)
    F["rsi14"] = (ind.rsi(c, 14) - 50) / 50
    F["rsi56"] = (ind.rsi(c, 56) - 50) / 50
    F["macd_h"] = ind.macd(c)[2] / c
    F["atr_pct"] = ind.atr(h, lo, c, 14) / c
    r1 = F["r1"]
    F["rv24"] = ind.rolling_std(np.nan_to_num(r1), 24)
    F["rv168"] = ind.rolling_std(np.nan_to_num(r1), 168)
    F["rv_ratio"] = F["rv24"] / F["rv168"]
    blo, bmid, bhi = ind.bollinger(c, 20, 2)
    F["bb_pos"] = (c - bmid) / ((bhi - blo) / 2)
    F["vol_z"] = np.log((v + 1) / (ind.sma(v, 168) + 1))
    mx, mn = ind.rolling_max(h, 168), ind.rolling_min(lo, 168)
    F["range_pos"] = (c - mn) / np.where(mx - mn > 0, mx - mn, np.nan) - 0.5
    hr = (t % 86400) / 3600
    dow = ((t // 86400) + 4) % 7  # 1970-01-01 was a Thursday
    F["hour_sin"], F["hour_cos"] = np.sin(2 * np.pi * hr / 24), np.cos(2 * np.pi * hr / 24)
    F["dow_sin"], F["dow_cos"] = np.sin(2 * np.pi * dow / 7), np.cos(2 * np.pi * dow / 7)
    F["funding"] = np.zeros(n)
    F["funding_avg3"] = np.zeros(n)
    if funding:
        ft = np.array([x["t"] for x in funding], dtype=float)
        fv = np.array([x["v"] for x in funding], dtype=float)
        idx = np.searchsorted(ft, t, side="right") - 1
        ok = idx >= 0
        F["funding"][ok] = fv[idx[ok]]
        avg3 = np.convolve(fv, np.ones(3) / 3, mode="full")[:len(fv)]
        F["funding_avg3"][ok] = avg3[idx[ok]]
    F["btc_r24"] = np.zeros(n)
    F["btc_d_ema50"] = np.zeros(n)
    if btc:
        bt = {r["t"]: i for i, r in enumerate(btc)}
        bc = np.array([r["c"] for r in btc], dtype=float)
        blc = np.log(bc)
        b24 = np.full(len(bc), np.nan)
        b24[24:] = blc[24:] - blc[:-24]
        bde = bc / ind.ema(bc, 50) - 1
        for i, ts in enumerate(t):
            j = bt.get(int(ts))
            if j is not None:
                F["btc_r24"][i] = 0 if np.isnan(b24[j]) else b24[j]
                F["btc_d_ema50"][i] = 0 if np.isnan(bde[j]) else bde[j]
    # taker flow: share of quote volume bought by aggressive buyers (0.5 = balanced). Missing (non-Binance) -> 0.
    tb = np.array([r.get("tb", np.nan) for r in rows], dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(v > 0, tb / v, np.nan) - 0.5
    share = np.nan_to_num(share, nan=0.0)
    F["tk4"] = ind.sma(share, 4)
    F["tk24"] = ind.sma(share, 24)
    X = np.column_stack([F[k] for k in FEATURES])
    X = np.clip(X, -50, 50)
    return t, c, X


def ewma_sigma(c, span=EWMA_SPAN):
    """Per-bar EWMA volatility of 1h log returns (aligned to c; sigma[i] uses returns up to i)."""
    r = np.diff(np.log(c), prepend=np.log(c[0]))
    lam = 1 - 2 / (span + 1)
    out = np.empty(len(c))
    var = float(np.var(r[1:min(len(r), span + 1)])) if len(r) > 2 else 1e-6
    for i, x in enumerate(r):
        var = lam * var + (1 - lam) * x * x
        out[i] = math.sqrt(var)
    return out


def cone_quantiles(c, hz, lookback=8760):
    """Filtered historical simulation: quantiles of h-hour log returns standardised by the vol at issue time."""
    sig = ewma_sigma(c)
    lc = np.log(c)
    n = len(c)
    start = max(EWMA_SPAN * 2, n - lookback - hz)
    idx = np.arange(start, n - hz)
    if len(idx) < 500:
        return None
    z = (lc[idx + hz] - lc[idx]) / (sig[idx] * math.sqrt(hz))
    z = z[np.isfinite(z)]
    emp = np.quantile(z, QUANTILES)
    # few independent windows -> noisy tails: blend toward the normal quantiles by effective sample size
    n_eff = len(z) / hz
    w = n_eff / (n_eff + 50)
    norm = np.array([_NORM_Z[q] for q in QUANTILES])
    return [float(x) for x in w * emp + (1 - w) * norm]


def labels(c, hz):
    y = np.full(len(c), np.nan)
    y[:-hz] = (c[hz:] > c[:-hz]).astype(float)
    return y


# ------------------------------------------------------------------ models
def _logit():
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=500))


def _gbm():
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.04, max_iter=180, min_samples_leaf=200,
                                          l2_regularization=2.0, random_state=7)


def _brier(p, y):
    return float(np.mean((p - y) ** 2))


def _platt_fit(p, y):
    """Fit a, b in sigmoid(a * logit(p) + b) via a tiny logistic regression."""
    p = np.clip(p, 1e-4, 1 - 1e-4)
    z = np.log(p / (1 - p)).reshape(-1, 1)
    lr = LogisticRegression(C=10.0).fit(z, y.astype(int))
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def _platt(p, a, b):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    z = a * np.log(p / (1 - p)) + b
    return 1 / (1 + np.exp(-z))


def _hedge(briers, eta=40.0):
    best = min(briers.values())
    w = {k: math.exp(-eta * (b - best)) for k, b in briers.items()}
    s = sum(w.values())
    w = {k: max(v / s, W_FLOOR) for k, v in w.items()}
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def train_one(sym, hz, t, c, X):
    """Purged walk-forward CV + final fit. Returns metadata; models saved with joblib."""
    y = labels(c, hz)
    valid = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
    idx = np.where(valid)[0]
    if len(idx) < 2000:
        raise ValueError(f"not enough history ({len(idx)} rows)")
    Xv, yv = X[idx], y[idx]
    n = len(idx)
    folds = 5
    start = int(n * 0.5)
    edges = np.linspace(start, n, folds + 1).astype(int)
    oos = {m: [] for m in ("base", "logit", "gbm")}
    oos_y = []
    for i in range(folds):
        a, b = edges[i], edges[i + 1]
        tr_end = max(a - hz, 100)  # purge: drop the hz rows whose labels overlap the test window
        Xtr, ytr = Xv[:tr_end], yv[:tr_end]
        Xte, yte = Xv[a:b], yv[a:b]
        base_p = float(ytr[-5000:].mean())
        oos["base"].append(np.full(len(yte), base_p))
        oos["logit"].append(_logit().fit(Xtr, ytr).predict_proba(Xte)[:, 1])
        oos["gbm"].append(_gbm().fit(Xtr, ytr).predict_proba(Xte)[:, 1])
        oos_y.append(yte)
    Y = np.concatenate(oos_y)
    P = {m: np.concatenate(v) for m, v in oos.items()}
    briers = {m: _brier(p, Y) for m, p in P.items()}
    # Honest score: prequential. Fold i is scored with ensemble weights + Platt fitted ONLY on folds < i.
    # (Fitting them on the same OOS data they are scored on leaks the test period's drift into "skill".)
    ev_p, ev_b, ev_y = [], [], []
    for i in range(1, folds):
        prevY = np.concatenate(oos_y[:i])
        prevP = {m: np.concatenate(oos[m][:i]) for m in oos}
        wi = _hedge({m: _brier(prevP[m], prevY) for m in prevP})
        ai, bi = _platt_fit(sum(wi[m] * prevP[m] for m in prevP), prevY)
        ev_p.append(_platt(sum(wi[m] * oos[m][i] for m in oos), ai, bi))
        ev_b.append(oos["base"][i])
        ev_y.append(oos_y[i])
    EP, EB, EY = np.concatenate(ev_p), np.concatenate(ev_b), np.concatenate(ev_y)
    # live parameters use all OOS evidence
    w = _hedge(briers)
    ens = sum(w[m] * P[m] for m in P)
    a_, b_ = _platt_fit(ens, Y)
    meta = {
        "sym": sym, "horizon_h": hz, "trained_at": time.time(), "n_train": int(n), "n_oos": int(len(EY)),
        "features": FEATURES, "zq": cone_quantiles(c, hz),
        "n_eff": int(len(EY) / hz),  # overlapping labels: ~independent samples
        "brier_oos": {**briers, "ensemble_cal": _brier(EP, EY), "base_eval": _brier(EB, EY)},
        "hit_oos": float(np.mean((EP > 0.5) == (EY > 0.5))),
        "base_rate": float(yv[-5000:].mean()), "weights_cv": w, "platt": [a_, b_],
    }
    meta["skill_oos"] = 1 - meta["brier_oos"]["ensemble_cal"] / meta["brier_oos"]["base_eval"]
    logit, gbm = _logit().fit(Xv, yv), _gbm().fit(Xv, yv)
    joblib.dump({"logit": logit, "gbm": gbm, "meta": meta}, MODEL_DIR / f"m_{sym}_{hz}.joblib")
    return meta


async def retrain(symbols, horizons):
    async with _lock:
        btc = closed(await history("BTC"))
        results = []
        for sym in symbols:
            try:
                rows = btc if sym == "BTC" else closed(await history(sym))
                try:
                    fund = await crypto.funding_history(sym, 1000)
                except Exception:  # noqa: BLE001
                    fund = None
                t, c, X = build_features(rows, fund, None if sym == "BTC" else btc)
                for hz in horizons:
                    meta = await asyncio.to_thread(train_one, sym, hz, t, c, X)
                    db.x("INSERT INTO model_runs(ts, symbol, horizon_h, metrics) VALUES (?,?,?,?)",
                         (time.time(), sym, hz, json.dumps(meta)))
                    st = _load_state()
                    key = f"{sym}:{hz}"
                    cur = st.get(key, {})
                    # keep live-learned weights if we have enough live evidence, else reset to CV weights
                    if cur.get("n_live", 0) < 30:
                        cur["weights"] = {**meta["weights_cv"], "online": W_FLOOR}
                    cur["meta"] = {k: meta[k] for k in ("trained_at", "n_train", "n_oos", "n_eff", "brier_oos",
                                                        "hit_oos", "base_rate", "platt", "skill_oos", "weights_cv",
                                                        "zq", "features")}
                    cur.setdefault("width", 1.0)
                    st[key] = cur
                    _save_state(st)
                    results.append(meta)
            except Exception as e:  # noqa: BLE001
                results.append({"sym": sym, "error": str(e)})
        return results


# ------------------------------------------------------------------ online model (live-only features)
def _online_path(sym, hz):
    return MODEL_DIR / f"online_v{ONLINE_VERSION}_{sym}_{hz}.joblib"


def _online_load(sym, hz):
    p = _online_path(sym, hz)
    if p.exists():
        m = joblib.load(p)
        if m.get("features") == FEATURES + LIVE_EXTRA:
            return m
    return {"scaler": StandardScaler(), "clf": SGDClassifier(loss="log_loss", alpha=1e-3, random_state=7), "n": 0,
            "classes_seen": set(), "features": FEATURES + LIVE_EXTRA}


def _online_vec(feat):
    return np.array([[feat.get(k, 0.0) or 0.0 for k in FEATURES + LIVE_EXTRA]], dtype=float)


def _online_predict(sym, hz, feat, base):
    m = _online_load(sym, hz)
    if m["n"] < 30 or len(m["classes_seen"]) < 2:
        return base
    x = m["scaler"].transform(_online_vec(feat))
    return float(m["clf"].predict_proba(x)[0, 1])


def _online_update(sym, hz, feat, y):
    m = _online_load(sym, hz)
    x = _online_vec(feat)
    m["scaler"].partial_fit(x)
    m["clf"].partial_fit(m["scaler"].transform(x), [int(y)], classes=[0, 1])
    m["n"] += 1
    m["classes_seen"].add(int(y))
    joblib.dump(m, _online_path(sym, hz))


# ------------------------------------------------------------------ issue
async def _live_extras(sym):
    from .sources import elfa
    out = dict(elfa.features(sym))
    agg = sentiment.aggregate()
    s = next((r for r in agg["rows"] if r["sym"] == sym), None)
    out["sent"] = s["sentiment"] if s else 0.0
    out["sent_mentions"] = math.log1p(s["mentions"]) if s else 0.0
    try:
        out["fng"] = ((await sentiment.crypto_fear_greed())["value"] - 50) / 50
    except Exception:  # noqa: BLE001
        out["fng"] = 0.0
    try:
        pos = await crypto.positioning(sym)
        out["oi_chg24"] = (pos.get("oi_chg24") or 0) / 100
        out["ls_retail"] = (pos["ls_retail"][-1]["v"] - 1) if pos.get("ls_retail") else 0.0
    except Exception:  # noqa: BLE001
        out["oi_chg24"], out["ls_retail"] = 0.0, 0.0
    return out


async def issue(symbols, horizons):
    """Issue one forecast per symbol x horizon on the last closed 1h bar (idempotent per hour)."""
    now = time.time()
    hour = now // 3600 * 3600
    issued = []
    st = _load_state()
    btc_rows = closed((await crypto.klines("BTC", "1h", 1000))["rows"], now)
    for sym in symbols:
        try:
            rows = btc_rows if sym == "BTC" else closed((await crypto.klines(sym, "1h", 1000))["rows"], now)
            if len(rows) < 400:
                continue
            try:
                fund = await crypto.funding_history(sym, 100)
            except Exception:  # noqa: BLE001
                fund = None
            t, c, X = build_features(rows, fund, None if sym == "BTC" else btc_rows)
            x = X[-1:]
            if np.isnan(x).any():
                continue
            price = float(c[-1])
            feat = dict(zip(FEATURES, map(float, x[0])))
            feat.update(await _live_extras(sym))
            sigma_now = float(ewma_sigma(c)[-1])
            for hz in horizons:
                if db.q("SELECT 1 FROM predictions WHERE symbol=? AND horizon_h=? AND source='ensemble' AND issued>=?",
                        (sym, hz, hour)):
                    continue
                path = MODEL_DIR / f"m_{sym}_{hz}.joblib"
                if not path.exists():
                    continue
                bundle = joblib.load(path)
                meta = bundle["meta"]
                if meta.get("features") != FEATURES:
                    continue  # trained on an older feature set; the loop retrains it first
                key = f"{sym}:{hz}"
                w = (st.get(key) or {}).get("weights") or {**meta["weights_cv"], "online": W_FLOOR}
                base = meta["base_rate"]
                comp = {"base": base, "logit": float(bundle["logit"].predict_proba(x)[0, 1]),
                        "gbm": float(bundle["gbm"].predict_proba(x)[0, 1]),
                        "online": _online_predict(sym, hz, feat, base)}
                ws = sum(w.get(m, 0) for m in comp)
                raw = sum(w.get(m, 0) * comp[m] for m in comp) / ws
                a_, b_ = meta["platt"]
                p_cal = float(_platt(np.array([raw]), a_, b_)[0])
                # honesty shrink: a model only gets to deviate from the base rate as far as its proven skill
                # allows, discounted further when that skill rests on few independent samples
                live = scoreboard_one(sym, hz)
                use_live = live["n"] / hz >= 30 and live["skill"] is not None
                skill = live["skill"] if use_live else meta.get("skill_oos")
                n_eff = (live["n"] / hz) if use_live else (meta.get("n_eff") or 0)
                shrink = float(np.clip((skill or 0) / 0.01, 0.15, 1.0) * min(1.0, n_eff / 200))
                shrink = max(shrink, 0.15)
                p = base + shrink * (p_cal - base)
                width = float((st.get(key) or {}).get("width", 1.0))
                q = cone(price, sigma_now, hz, meta.get("zq"), width)
                comp_out = {**comp, "raw": raw, "cal": p_cal, "shrink": shrink, "weights": w, "q": q,
                            "width": width, "sigma_h": sigma_now, "range80": [q["10"], q["90"]]}
                db.x("INSERT INTO predictions(issued, symbol, horizon_h, p_up, p_base, components, features, price,"
                     " resolve_at, source) VALUES (?,?,?,?,?,?,?,?,?, 'ensemble')",
                     (hour + 60, sym, hz, p, base, json.dumps(comp_out), json.dumps(feat), price, hour + hz * 3600))
                issued.append({"sym": sym, "h": hz, "p": p})
        except Exception as e:  # noqa: BLE001
            issued.append({"sym": sym, "error": str(e)})
    return issued


def adapt_width(width, in80, hz):
    """Online coverage calibration of the cone (stochastic approximation toward 20% misses outside 10-90%).
    A miss widens by ~e^(0.8*eta), a hit narrows by ~e^(-0.2*eta): the fixed point is exactly 80% coverage.
    eta shrinks with the horizon because overlapping long-horizon outcomes are highly correlated."""
    eta = 0.04 / math.sqrt(max(hz, 4) / 4)
    return float(np.clip(width * math.exp(eta * ((1 - in80) - 0.2)), 0.5, 2.5))


def cone(price, sigma_h, hz, zq=None, width=1.0):
    """Price quantiles at t+hz. Empirical standardised quantiles (fat tails) when trained, else normal.
    `width` is the online coverage calibrator: it widens after misses, narrows after hits (target 80% in 10-90)."""
    zs = zq if zq and len(zq) == len(QUANTILES) else [_NORM_Z[qq] for qq in QUANTILES]
    centre = zs[QUANTILES.index(0.5)]
    s = sigma_h * math.sqrt(hz)
    return {f"{int(round(qq * 100)):02d}": price * math.exp(s * (centre + (z - centre) * width))
            for qq, z in zip(QUANTILES, zs)}


def log_llm_forecast(sym, hz, p_up, note=""):
    now = time.time()
    try:
        price = None
        rows = db.q("SELECT price FROM predictions WHERE symbol=? ORDER BY issued DESC LIMIT 1", (sym,))
        if rows:
            price = rows[0]["price"]
    except Exception:  # noqa: BLE001
        price = None
    return db.x("INSERT INTO predictions(issued, symbol, horizon_h, p_up, p_base, components, price, resolve_at, source,"
                " note) VALUES (?,?,?,?,?,?,?,?, 'llm', ?)",
                (now, sym, hz, p_up, 0.5, "{}", price, now + hz * 3600, note[:300]))


# ------------------------------------------------------------------ grade + adapt
async def grade():
    now = time.time()
    due = db.q("SELECT * FROM predictions WHERE outcome IS NULL AND resolve_at <= ? ORDER BY resolve_at LIMIT 200",
               (now - 120,))
    graded = 0
    st = _load_state()
    for p in due:
        try:
            if p["price"] is None:  # LLM call issued without a reference price: take it at issue time
                p0 = await crypto.price_at(p["symbol"], (p["issued"] // 1800 + 1) * 1800)
                if p0 is None:
                    continue
                db.x("UPDATE predictions SET price=? WHERE id=?", (p0, p["id"]))
                p["price"] = p0
            pe = await crypto.price_at(p["symbol"], p["resolve_at"])
        except Exception:  # noqa: BLE001
            continue
        if pe is None:
            continue
        y = 1 if pe > p["price"] else 0
        brier = (p["p_up"] - y) ** 2
        brier_base = (p["p_base"] - y) ** 2
        comp = json.loads(p["components"] or "{}")
        q = comp.get("q") or {}
        in80 = int(q["10"] <= pe <= q["90"]) if q.get("10") and q.get("90") else None
        db.x("UPDATE predictions SET outcome=?, price_end=?, ret=?, brier=?, brier_base=?, in80=? WHERE id=?",
             (y, pe, pe / p["price"] - 1, brier, brier_base, in80, p["id"]))
        graded += 1
        if p["source"] != "ensemble":
            continue
        key = f"{p['symbol']}:{p['horizon_h']}"
        cur = st.setdefault(key, {})
        if in80 is not None:
            cur["width"] = adapt_width(cur.get("width", 1.0), in80, p["horizon_h"])
        w = cur.get("weights") or {m: 1 / len(MODELS) for m in MODELS}
        for m in MODELS:
            if m in comp:
                w[m] = w.get(m, W_FLOOR) * math.exp(-ETA * (comp[m] - y) ** 2)
        s = sum(w.values())
        w = {k: max(v / s, W_FLOOR) for k, v in w.items()}
        s = sum(w.values())
        cur["weights"] = {k: v / s for k, v in w.items()}
        cur["n_live"] = cur.get("n_live", 0) + 1
        try:
            feat = json.loads(p["features"] or "{}")
            if feat:
                await asyncio.to_thread(_online_update, p["symbol"], p["horizon_h"], feat, y)
        except Exception:  # noqa: BLE001
            pass
    if graded:
        _save_state(st)
    return graded


# ------------------------------------------------------------------ reporting
def _edge(skill, n):
    """n = effective independent samples behind the skill estimate."""
    if skill is None:
        return "untested"
    if n is not None and n < 30:
        return "too early"
    if skill < 0.002:
        return "no edge"
    if n is not None and n < 100:
        return "weak (low sample)"
    if skill < 0.01:
        return "weak"
    if skill < 0.03:
        return "moderate"
    return "strong (verify)"


def current():
    st = _load_state()
    rows = db.q("SELECT * FROM predictions WHERE source='ensemble' AND issued > ? ORDER BY issued DESC",
                (time.time() - 3 * 3600,))
    seen, out = set(), []
    for r in rows:
        k = (r["symbol"], r["horizon_h"])
        if k in seen:
            continue
        seen.add(k)
        meta = (st.get(f"{r['symbol']}:{r['horizon_h']}") or {}).get("meta") or {}
        comp = json.loads(r["components"] or "{}")
        live = scoreboard_one(r["symbol"], r["horizon_h"])
        live_eff = live["n"] / max(r["horizon_h"], 1) if live["n"] else 0
        use_live = live_eff >= 30
        out.append({"sym": r["symbol"], "h": r["horizon_h"], "p_up": r["p_up"], "p_base": r["p_base"],
                    "price": r["price"], "issued": r["issued"], "resolve_at": r["resolve_at"],
                    "range80": comp.get("range80"), "cone": comp.get("q"), "width": comp.get("width"),
                    "coverage": coverage_one(r["symbol"], r["horizon_h"]),
                    "components": {m: comp.get(m) for m in MODELS},
                    "p_cal": comp.get("cal"), "shrink": comp.get("shrink"),
                    "weights": comp.get("weights"), "skill_oos": meta.get("skill_oos"), "n_eff": meta.get("n_eff"),
                    "hit_oos": meta.get("hit_oos"), "live": live,
                    "edge": _edge(live["skill"] if use_live else meta.get("skill_oos"),
                                  live_eff if use_live else meta.get("n_eff"))})
    out.sort(key=lambda r: (r["sym"], r["h"]))
    return out


def coverage_one(sym, hz, days=90):
    """How often the realised price landed inside the 10-90% cone (target 80%)."""
    rows = db.q("SELECT in80 FROM predictions WHERE symbol=? AND horizon_h=? AND source='ensemble' AND in80 IS NOT NULL"
                " AND issued > ?", (sym, hz, time.time() - days * 86400))
    n = len(rows)
    return {"n": n, "rate": (sum(r["in80"] for r in rows) / n) if n else None, "target": 0.8}


def scoreboard_one(sym, hz, source="ensemble", days=90):
    rows = db.q("SELECT p_up, outcome, brier, brier_base FROM predictions WHERE symbol=? AND horizon_h=? AND source=?"
                " AND outcome IS NOT NULL AND issued > ?", (sym, hz, source, time.time() - days * 86400))
    n = len(rows)
    if not n:
        return {"n": 0, "brier": None, "brier_base": None, "skill": None, "hit": None}
    b = sum(r["brier"] for r in rows) / n
    bb = sum(r["brier_base"] for r in rows) / n
    hit = sum((r["p_up"] > 0.5) == (r["outcome"] == 1) for r in rows) / n
    return {"n": n, "brier": b, "brier_base": bb, "skill": (1 - b / bb) if bb else None, "hit": hit}


def scoreboard():
    st = _load_state()
    keys = sorted({(r["symbol"], r["horizon_h"], r["source"]) for r in
                   db.q("SELECT DISTINCT symbol, horizon_h, source FROM predictions")})
    rows = []
    for sym, hz, src in keys:
        live = scoreboard_one(sym, hz, src)
        meta = (st.get(f"{sym}:{hz}") or {}).get("meta") or {} if src == "ensemble" else {}
        rows.append({"sym": sym, "h": hz, "source": src, "live": live, "oos": meta,
                     "coverage": coverage_one(sym, hz) if src == "ensemble" else None,
                     "width": (st.get(f"{sym}:{hz}") or {}).get("width") if src == "ensemble" else None,
                     "weights": (st.get(f"{sym}:{hz}") or {}).get("weights") if src == "ensemble" else None,
                     "n_live": (st.get(f"{sym}:{hz}") or {}).get("n_live", 0)})
    # calibration buckets across everything resolved
    res = db.q("SELECT p_up, outcome, source FROM predictions WHERE outcome IS NOT NULL")
    cal = {}
    for src in ("ensemble", "llm"):
        buckets = [[0, 0, 0.0] for _ in range(10)]
        for r in res:
            if r["source"] != src:
                continue
            i = min(int(r["p_up"] * 10), 9)
            buckets[i][0] += 1
            buckets[i][1] += r["outcome"]
            buckets[i][2] += r["p_up"]
        cal[src] = [{"bucket": i / 10, "n": b[0], "freq": b[1] / b[0] if b[0] else None,
                     "mean_p": b[2] / b[0] if b[0] else None} for i, b in enumerate(buckets)]
    pending = db.q("SELECT COUNT(*) n FROM predictions WHERE outcome IS NULL")[0]["n"]
    return {"rows": rows, "calibration": cal, "pending": pending}


def llm_track_record():
    rows = db.q("SELECT p_up, outcome FROM predictions WHERE source='llm' AND outcome IS NOT NULL")
    n = len(rows)
    if n < 5:
        return {"n": n, "text": f"Your graded probability calls so far: {n} (too few to judge calibration)."}
    b = sum((r["p_up"] - r["outcome"]) ** 2 for r in rows) / n
    bb = sum((0.5 - r["outcome"]) ** 2 for r in rows) / n
    # shrink factor: least squares of (y - .5) on (p - .5); <1 means calls are too bold
    num = sum((r["p_up"] - 0.5) * (r["outcome"] - 0.5) for r in rows)
    den = sum((r["p_up"] - 0.5) ** 2 for r in rows) or 1e-9
    alpha = num / den
    hint = ("too bold: move your probabilities closer to 50%" if alpha < 0.8 else
            "too timid: you can commit more" if alpha > 1.2 else "reasonably calibrated")
    return {"n": n, "brier": b, "brier_coin": bb, "alpha": alpha,
            "text": f"Your graded probability calls: n={n}, Brier {b:.3f} vs coin-flip {bb:.3f}, "
                    f"shrink factor {alpha:.2f} ({hint})."}


def history_for(sym, hz=None, limit=200):
    sql = "SELECT id, issued, horizon_h, p_up, p_base, price, resolve_at, outcome, price_end, brier, brier_base, source," \
          " note FROM predictions WHERE symbol=?"
    args: list = [sym]
    if hz:
        sql += " AND horizon_h=?"
        args.append(hz)
    sql += " ORDER BY issued DESC LIMIT ?"
    args.append(limit)
    return db.q(sql, args)


def models_status(symbols, horizons):
    st = _load_state()
    out = []
    for s in symbols:
        for h in horizons:
            e = st.get(f"{s}:{h}") or {}
            out.append({"sym": s, "h": h, "trained": (MODEL_DIR / f"m_{s}_{h}.joblib").exists()
                        and (e.get("meta") or {}).get("features") == FEATURES,
                        "meta": e.get("meta"), "weights": e.get("weights"), "n_live": e.get("n_live", 0)})
    return out
