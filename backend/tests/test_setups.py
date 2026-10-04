"""Setups must be causal (no look-ahead) and their statistics honest."""
import numpy as np

from rookery import setups


def bars(closes, vol=None, t0=1_700_000_000, step=3600):
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"t": t0 + i * step, "o": o, "h": max(o, c) * 1.002, "l": min(o, c) * 0.998, "c": c,
                    "v": (vol[i] if vol is not None else 100.0)})
        prev = c
    return out


def random_walk(n=1500, seed=7, drift=0.0):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n))))


def test_detectors_have_no_lookahead():
    rows = bars(random_walk(900, seed=3))
    full = setups.detect(rows)
    for k in (260, 400, 555, 731, 899):
        part = setups.detect(rows[:k])
        for name in setups.SETUPS:
            assert part[name][-1] == full[name][k - 1], f"{name} changed at bar {k - 1} when future bars were added"


def test_breakout_on_volume_fires_once():
    closes = [100.0] * 120 + [100.5, 101, 104]  # flat range, then a close above the prior 50-bar high
    vol = [100.0] * 122 + [400.0]
    rows = bars(closes, vol)
    cond = setups.detect(rows)["breakout"]
    assert cond[-1]
    assert not cond[:-1].any()


def test_pullbacks_follow_the_trend():
    up = setups.detect(bars(random_walk(1500, seed=5, drift=0.0015)))
    down = setups.detect(bars(random_walk(1500, seed=5, drift=-0.0015)))
    assert up["pullback_up"].sum() > 5 and up["pullback_up"].sum() > 3 * up["pullback_down"].sum()
    assert down["pullback_down"].sum() > 5 and down["pullback_down"].sum() > 3 * down["pullback_up"].sum()


def test_onsets_respect_cooldown():
    cond = np.zeros(100, dtype=bool)
    cond[[10, 11, 12, 15, 30, 31, 60]] = True
    assert list(setups.onsets(cond, 12)) == [10, 30, 60]


def test_backtest_reports_base_rate_and_honest_verdicts():
    rows = bars(random_walk(3000, seed=11))
    st = setups.backtest(rows, "1h")
    assert st, "backtest should produce stats on 3000 bars"
    for name, s in st.items():
        if s["n"] == 0:
            assert s["verdict"] == "too few"
            continue
        assert 0 <= s["hit"] <= 1 and 0 <= s["base"] <= 1
        assert s["edge"] == s["hit"] - s["base"]
        if s["n"] < 15:
            assert s["verdict"] == "too few"
    # a random walk must not show "historically favorable" for most setups
    favourable = [k for k, s in st.items() if s.get("verdict") == "historically favorable"]
    assert len(favourable) <= 2, favourable


def test_neutral_setup_verdict_wording():
    assert setups._verdict(200, 0.12, 0.03, "neutral") == "big moves more likely"
    assert setups._verdict(200, -0.12, 0.03, "neutral") == "quiet tends to persist"
    assert setups._verdict(200, 0.01, 0.03, "bull") == "no clear edge"
    assert setups._verdict(10, 0.4, 0.01, "bull") == "too few"


def test_levels_cluster_pivots():
    wave = []
    for _ in range(8):  # oscillate between ~90 and ~110 so pivots cluster at both ends
        wave += list(np.linspace(90, 110, 12)) + list(np.linspace(110, 90, 12))
    rows = bars(wave + [100.0])
    lv = setups.levels(rows, lookback=180)
    assert lv["support"] and lv["resistance"]
    assert lv["support"][0]["price"] < 100 < lv["resistance"][0]["price"]
    assert max(e["touches"] for e in lv["resistance"]) >= 3


def test_active_marks_recent_onsets_only():
    closes = [100.0] * 120 + [100.5, 101, 104]
    vol = [100.0] * 122 + [400.0]
    act = setups.active(bars(closes, vol), "1h")
    assert any(a["id"] == "breakout" and a["age_bars"] == 0 for a in act)
