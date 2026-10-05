"""Markets: metric math (returns, bp, BRL conversion, vs CDI), CDI accumulation, batching, aliases, boards offline."""
import json
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from rookery import ai
from rookery.config import load_settings
from rookery.sources import markets
from tests.conftest import run

DAY = 86400


def series(values, end=None, step=DAY):
    end = end or time.time()
    n = len(values)
    return [(int(end - (n - 1 - i) * step), float(v)) for i, v in enumerate(values)]


def test_returns_use_calendar_windows():
    now = time.time()
    pts = series(np.linspace(100, 200, 400), end=now)  # +0.25 per day
    m = markets.metrics(pts, now=now)
    per_day = 100 / 399
    assert m["price"] == pytest.approx(200)
    assert m["w1"] == pytest.approx((200 / (200 - 7 * per_day) - 1) * 100, rel=1e-3)
    assert m["y1"] == pytest.approx((200 / (200 - 365 * per_day) - 1) * 100, rel=1e-3)
    assert m["trend"] == "up" and m["pos52"] == pytest.approx(1.0)


def test_short_history_gives_none_not_garbage():
    pts = series([10, 11, 12, 13, 14, 15])
    m = markets.metrics(pts)
    assert m["y1"] is None and m["m1"] is None and m["w1"] is not None


def test_yields_change_in_basis_points():
    pts = series([4.00] * 40 + [4.25])
    m = markets.metrics(pts, unit="yield")
    assert m["w1"] == pytest.approx(25)
    assert m["brl"] is None and m["vol30"] is None


def test_brl_conversion_and_excess_over_cdi():
    now = time.time()
    asset = series([100.0] * 60 + [110.0], end=now)  # +10% in USD
    fx = series([5.0] * 60 + [5.5], end=now)  # USD/BRL +10%
    cdi = markets.CDI([(date.today() - timedelta(days=i), 0.05) for i in range(120, -1, -1)])
    m = markets.metrics(asset, ccy="USD", fx=fx, fx_now=5.5, cdi=cdi, now=now)
    assert m["m1"] == pytest.approx(10)
    assert m["brl"]["m1"] == pytest.approx(21)  # 1.1 * 1.1
    acc = cdi.since(datetime.fromtimestamp(now - 30 * DAY, timezone.utc).date())
    assert m["xcdi"]["m1"] == pytest.approx((1.21 / (1 + acc) - 1) * 100)


def test_us_cents_convert_like_dollars():
    now = time.time()
    asset = series([1000.0] * 40 + [1100.0], end=now)  # soybeans quoted in US cents
    fx = series([5.0] * 40 + [5.5], end=now)
    assert markets.metrics(asset, ccy="USX", fx=fx, fx_now=5.5, now=now)["brl"]["w1"] == pytest.approx(21)


def test_brl_assets_need_no_fx_and_other_currencies_get_none():
    pts = series([100.0] * 40 + [105.0])
    assert markets.metrics(pts, ccy="BRL")["brl"]["w1"] == pytest.approx(5)
    assert markets.metrics(pts, ccy="EUR")["brl"] is None


def test_cdi_accumulation():
    d0 = date(2026, 1, 1)
    cdi = markets.CDI([(d0 + timedelta(days=i), 0.05) for i in range(10)])
    assert cdi.since(d0) == pytest.approx(1.0005 ** 9 - 1)
    assert cdi.since(d0 - timedelta(days=60)) is None  # before the data starts


def test_kind_and_alias_detection():
    assert markets.kind_of("GC=F") == "commodity" and markets.kind_of("^GSPC") == "index"
    assert markets.kind_of("BRL=X") == "fx" and markets.kind_of("^TNX") == "rate"
    assert markets.kind_of("PETR4.SA") == "equity" and markets.kind_of("XYZ=F") == "commodity"
    assert markets.detect_aliases("gold or oil? o dólar e o Ibovespa, S&P 500") == ["GC=F", "CL=F", "BRL=X", "^BVSP", "^GSPC"]
    assert markets.detect_aliases("the goldman report") == []  # word boundaries
    assert markets.is_yahoo_symbol("GC=F") and markets.is_yahoo_symbol("BRK-B") and not markets.is_yahoo_symbol("BTC")


def test_ai_routes_commodities_to_the_stock_path():
    st = load_settings()
    syms = ai.detect_symbols("Should I buy gold or bitcoin?", st)
    assert "GC=F" in syms and "BTC" in syms
    crypto = [s for s in syms if not markets.is_yahoo_symbol(s) and s not in st["watchlist"]["stocks"]]
    assert crypto == ["BTC"]


def _spark_payload(syms, n=300):
    now = int(time.time())
    out = {}
    for i, s in enumerate(syms):
        ts = [now - (n - 1 - k) * DAY for k in range(n)]
        out[s] = {"symbol": s, "timestamp": ts, "close": [100 + i + k * 0.1 for k in range(n)]}
    return out


def test_history_batches_20_per_request_and_caches(router):
    def spark(req):
        syms = req.url.params["symbols"].split(",")
        assert len(syms) <= 20
        return _spark_payload(syms)
    router.add("GET", "/v8/finance/spark", spark)
    syms = [f"S{i}" for i in range(45)]
    h = run(markets.history(syms))
    assert len(h) == 45 and len(h["S0"]) == 300
    n_calls = len([c for c in router.calls if "spark" in str(c.url)])
    assert n_calls == 3
    run(markets.history(syms[:10]))  # served from the per-symbol cache
    assert len([c for c in router.calls if "spark" in str(c.url)]) == n_calls


def test_board_offline(router):
    def spark(req):
        return _spark_payload(req.url.params["symbols"].split(","))

    def quote(req):
        syms = req.url.params["symbols"].split(",")
        return {"quoteResponse": {"result": [
            {"symbol": s, "regularMarketPrice": 150.0, "regularMarketChangePercent": 1.5, "currency": "USD",
             "quoteType": "FUTURE", "shortName": s} for s in syms]}}

    def bcb(req):
        sid = req.url.path.split("bcdata.sgs.")[1].split("/")[0]
        today = date.today()
        if sid == "433":
            return [{"data": (today.replace(day=1) - timedelta(days=30 * i)).strftime("01/%m/%Y"), "valor": "0.40"}
                    for i in range(14, 0, -1)]
        return [{"data": (today - timedelta(days=i)).strftime("%d/%m/%Y"),
                 "valor": {"12": "0.05", "432": "13.75", "4389": "13.65"}[sid]} for i in range(40, -1, -1)]
    router.add("GET", "/v8/finance/spark", spark)
    router.add("GET", "/v7/finance/quote", quote)
    router.add("GET", "fc.yahoo.com", httpx.Response(200, text="ok"))
    router.add("GET", "/v1/test/getcrumb", httpx.Response(200, text="crumb123"))
    router.add("GET", "api.bcb.gov.br", bcb)
    b = run(markets.board("commodities"))
    names = [g["name"] for g in b["groups"]]
    assert names == ["Metals", "Energy", "Agriculture", "Livestock"]
    gold = b["groups"][0]["rows"][0]
    assert gold["sym"] == "GC=F" and gold["label"] == "Gold" and gold["kind"] == "commodity"
    assert gold["price"] == 150.0 and gold["chg1d"] == 1.5 and gold["m1"] is not None
    assert gold["brl"] is not None and gold["xcdi"] is not None and len(gold["spark"]) <= 65
    json.dumps(b)  # serialisable (no CDI object leaks)
    br = markets.public_brazil(run(markets.brazil()))
    assert br["selic"] == 13.75 and br["ipca12"] == pytest.approx((1.004 ** 12 - 1) * 100)
    assert "_cdi" not in br
    with pytest.raises(ValueError):
        run(markets.board("nope"))


def test_readout_lines_are_plain_language():
    sm = {"^GSPC": {"label": "S&P 500", "last": 5000, "m1": 3.0, "ytd": 10.0},
          "DX-Y.NYB": {"label": "DXY", "last": 105, "m1": 2.0},
          "^TNX": {"label": "10y", "last": 4.5, "m1": 30.0, "unit": "yield"},
          "GC=F": {"label": "Gold", "last": 2000, "m1": -1.0, "ytd": 5.0}}
    arr = {"^GSPC": np.linspace(4000, 5000, 260)}
    btc = [{"sym": "GC=F", "label": "Gold", "c30": 0.6, "c90": 0.45}, {"sym": "^NDX", "label": "Nasdaq", "c30": 0.1, "c90": 0.2}]
    br = {"selic": 13.75, "ipca12": 4.2, "real_rate": 9.2, "cdi_month": 1.07}
    lines = {x["k"]: x for x in markets._readout(sm, btc, arr, br)}
    assert lines["Stocks"]["tone"] == "bull" and "above its 200-day" in lines["Stocks"]["text"]
    assert lines["Dollar"]["tone"] == "bear" and lines["Rates"]["tone"] == "bear"
    assert "Gold" in lines["Bitcoin's driver"]["text"] and "getting stronger" in lines["Bitcoin's driver"]["text"]
    assert "1.07% a month" in lines["Brazil"]["text"]
