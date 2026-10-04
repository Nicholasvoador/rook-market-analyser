"""Vectorised indicators (numpy). All return arrays aligned to input, NaN during warm-up."""
import numpy as np


def ema(x, n):
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x, n):
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rolling_std(x, n):
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if len(x) >= n:
        w = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n - 1:] = w.std(axis=1)
    return out


def rolling_max(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(np.asarray(x, float), n).max(axis=1)
    return out


def rolling_min(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(np.asarray(x, float), n).min(axis=1)
    return out


def _wilder(x, n):
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if len(x) <= n:
        return out
    out[n] = x[1:n + 1].mean()
    for i in range(n + 1, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def rsi(c, n=14):
    c = np.asarray(c, dtype=float)
    d = np.diff(c, prepend=c[0])
    up = _wilder(np.where(d > 0, d, 0.0), n)
    dn = _wilder(np.where(d < 0, -d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / dn
        out = 100 - 100 / (1 + rs)
    out[(dn == 0) & ~np.isnan(up)] = 100.0
    return out


def macd(c, fast=12, slow=26, sig=9):
    m = ema(c, fast) - ema(c, slow)
    valid = ~np.isnan(m)
    s = np.full_like(m, np.nan)
    if valid.sum() >= sig:
        first = np.argmax(valid)
        s[first:] = ema(m[first:], sig)
    return m, s, m - s


def atr(h, l, c, n=14):
    h, l, c = (np.asarray(a, dtype=float) for a in (h, l, c))
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return _wilder(tr, n)


def bollinger(c, n=20, k=2.0):
    m = sma(c, n)
    sd = rolling_std(c, n)
    return m - k * sd, m, m + k * sd


def last(x):
    x = np.asarray(x, dtype=float)
    v = x[~np.isnan(x)]
    return float(v[-1]) if len(v) else None


def cross(a, b, lookback=3):
    """+1 if a crossed above b within lookback bars, -1 if below, 0 otherwise."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = np.sign(a - b)
    d = d[~np.isnan(d)]
    if len(d) < lookback + 1:
        return 0
    seg = d[-(lookback + 1):]
    if seg[-1] > 0 and (seg[:-1] <= 0).any():
        return 1
    if seg[-1] < 0 and (seg[:-1] >= 0).any():
        return -1
    return 0
