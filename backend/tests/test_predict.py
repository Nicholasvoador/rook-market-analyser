import math

import numpy as np
import pytest

from rookery import predict


def test_cone_quantiles_are_ordered_and_widen_with_horizon():
    q4 = predict.cone(100.0, 0.01, 4)
    q24 = predict.cone(100.0, 0.01, 24)
    keys = ["05", "10", "25", "50", "75", "90", "95"]
    assert [q4[k] for k in keys] == sorted(q4[k] for k in keys)
    assert q24["90"] - q24["10"] > q4["90"] - q4["10"]
    # normal fallback: 10-90 band = exp(+-1.2816 * sigma * sqrt(h))
    assert q24["90"] == pytest.approx(100 * math.exp(1.2816 * 0.01 * math.sqrt(24)), rel=1e-4)


def test_cone_width_multiplier_scales_dispersion_not_centre():
    a = predict.cone(100.0, 0.01, 24, width=1.0)
    b = predict.cone(100.0, 0.01, 24, width=1.5)
    assert b["50"] == pytest.approx(a["50"])
    assert b["90"] > a["90"] and b["10"] < a["10"]


def test_adapt_width_converges_to_80pct_coverage():
    rng = np.random.default_rng(0)
    w = 1.0
    true_scale = 1.6  # the market is 60% more volatile than the model thinks
    for _ in range(4000):
        z = rng.normal() * true_scale
        in80 = int(abs(z) <= 1.2816 * w)
        w = predict.adapt_width(w, in80, 4)
    assert w == pytest.approx(true_scale, rel=0.15)


def test_adapt_width_is_bounded():
    w = 1.0
    for _ in range(500):
        w = predict.adapt_width(w, 0, 4)
    assert w == 2.5
    for _ in range(5000):
        w = predict.adapt_width(w, 1, 4)
    assert w == 0.5


def test_cone_quantiles_shrink_to_normal_with_few_samples():
    rng = np.random.default_rng(3)
    assert predict.cone_quantiles(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600))), 168) is None  # too short
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1500)))
    zq = predict.cone_quantiles(c, 168)
    assert zq is not None
    norm = [predict._NORM_Z[q] for q in predict.QUANTILES]
    # ~600 overlapping windows / 168h = few independent samples -> mostly the normal quantiles
    assert max(abs(a - b) for a, b in zip(zq, norm)) < 0.6
    assert zq == sorted(zq)


def test_taker_features_present_and_neutral_when_missing():
    t0 = 1_700_000_000
    rows = [{"t": t0 + i * 3600, "o": 100, "h": 101, "l": 99, "c": 100 + math.sin(i / 5), "v": 1000.0} for i in range(400)]
    _, _, X = predict.build_features(rows)
    i = predict.FEATURES.index("tk24")
    assert np.nanmax(np.abs(X[:, i])) == 0  # no taker data -> balanced (0)
    for r in rows:
        r["tb"] = 700.0  # 70% aggressive buying
    _, _, X = predict.build_features(rows)
    assert X[-1, i] == pytest.approx(0.2)


def test_edge_labels_are_conservative():
    assert predict._edge(None, None) == "untested"
    assert predict._edge(0.05, 10) == "too early"
    assert predict._edge(0.001, 500) == "no edge"
    assert predict._edge(0.02, 60) == "weak (low sample)"
    assert predict._edge(0.02, 500) == "moderate"
