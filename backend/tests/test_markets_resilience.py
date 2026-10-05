"""Resilience: one flaky BCB series must not blank the whole Brazil block."""
from datetime import date, timedelta

import httpx

from rookery.sources import markets
from tests.conftest import run


def test_brazil_survives_a_failing_series_and_retries_soon(router):
    calls = {"433": 0}

    def bcb(req):
        sid = req.url.path.split("bcdata.sgs.")[1].split("/")[0]
        today = date.today()
        if sid == "433":  # IPCA: BCB answers with an HTML error page (twice: the retry fails too)
            calls["433"] += 1
            return httpx.Response(200, text="<html>erro</html>")
        return [{"data": (today - timedelta(days=i)).strftime("%d/%m/%Y"),
                 "valor": {"12": "0.05", "432": "13.75", "4389": "13.65"}[sid]} for i in range(40, -1, -1)]
    router.add("GET", "api.bcb.gov.br", bcb)
    br = run(markets.brazil())
    assert br["selic"] == 13.75 and br["cdi_aa"] == 13.65 and br["_cdi"] is not None
    assert br["ipca12"] is None and br["real_rate"] is None and br["partial"] is True
    assert calls["433"] == 2  # retried once
    age = markets.net.cache_age("brazil")
    assert age is not None and age > 3600 - 61  # partial result expires within a minute


def test_brazil_total_failure_is_not_cached(router):
    router.add("GET", "api.bcb.gov.br", httpx.Response(503, text="down"))
    try:
        run(markets.brazil())
        raise AssertionError("expected failure")
    except Exception as e:  # noqa: BLE001
        assert "BCB" in str(e) or "503" in str(e)
    assert markets.net.cache_age("brazil") is None
