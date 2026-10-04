import numpy as np
import pytest

from rookery import indicators as ind


def test_ema_matches_recursive_definition():
    x = np.arange(1, 31, dtype=float)
    e = ind.ema(x, 10)
    assert np.isnan(e[:9]).all()
    assert e[9] == pytest.approx(x[:10].mean())
    a = 2 / 11
    assert e[10] == pytest.approx(a * x[10] + (1 - a) * e[9])


def test_rsi_bounds_and_extremes():
    up = np.linspace(100, 200, 60)
    down = np.linspace(200, 100, 60)
    assert ind.last(ind.rsi(up)) == pytest.approx(100)
    assert ind.last(ind.rsi(down)) == pytest.approx(0, abs=1e-9)
    rng = np.random.default_rng(1)
    r = ind.rsi(100 + rng.normal(0, 1, 500).cumsum())
    v = r[~np.isnan(r)]
    assert (v >= 0).all() and (v <= 100).all()


def test_atr_and_bollinger_shapes():
    rng = np.random.default_rng(2)
    c = 100 + rng.normal(0, 1, 200).cumsum()
    h, lo = c + 1, c - 1
    a = ind.atr(h, lo, c, 14)
    assert ind.last(a) >= 2 - 1e-9  # high-low is always 2
    blo, mid, bhi = ind.bollinger(c)
    ok = ~np.isnan(mid)
    assert (blo[ok] <= mid[ok]).all() and (mid[ok] <= bhi[ok]).all()


def test_cross_detection():
    a = np.array([1, 1, 1, 1, 3.0])
    b = np.array([2, 2, 2, 2, 2.0])
    assert ind.cross(a, b, 3) == 1
    assert ind.cross(b, a, 3) == -1
    assert ind.cross(np.ones(5) * 3, np.ones(5), 3) == 0
