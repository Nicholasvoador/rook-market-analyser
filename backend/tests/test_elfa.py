"""Elfa: budget pacing, persistence (no re-spend), plan errors, SSE parsing."""
import json

import httpx
import pytest

from rookery import db
from rookery.config import save_settings, set_secrets
from rookery.sources import elfa
from tests.conftest import run


@pytest.fixture
def key():
    set_secrets({"ELFA_API_KEY": "elfak_test_not_real"})
    yield
    set_secrets({"ELFA_API_KEY": ""})


def status(router, monthly=0, tier="free"):
    router.add("GET", "/v2/key-status", {"success": True, "data": {"usage": {"monthly": monthly, "daily": 0},
                                                                    "limits": {"monthly": 1000, "daily": 1000},
                                                                    "tier": tier, "key": "SHOULD-NOT-BE-STORED"}})


def test_budget_pacing_blocks_auto_but_allows_on_demand(key):
    save_settings({"elfa": {"monthly_credits": 1000, "reserve_pct": 10}})
    b = elfa.Budget.status()
    assert b["limit"] == 1000 and b["used"] == 0 and b["daily_allowance"] > 0
    today = elfa._day()
    db.x("INSERT INTO api_usage(day, api, units) VALUES (?, 'elfa', ?)", (today, int(b["daily_allowance"]) + 1))
    ok_auto, why = elfa.Budget.allow(1, auto=True)
    ok_user, _ = elfa.Budget.allow(1, auto=False)
    assert not ok_auto and "daily pace" in why
    assert ok_user


def test_reserve_is_never_auto_spent(key):
    save_settings({"elfa": {"monthly_credits": 100, "reserve_pct": 10}})
    db.x("INSERT INTO api_usage(day, api, units) VALUES ('2000-01-01', 'elfa', 0)")
    elfa.kv_put("elfa:status", {"monthly": 91, "limit_month": 100, "local_since": 0})
    assert not elfa.Budget.allow(1, auto=True)[0]
    assert elfa.Budget.allow(1, auto=False)[0]
    elfa.kv_put("elfa:status", {"monthly": 99, "limit_month": 100, "local_since": 0})
    assert not elfa.Budget.allow(1, auto=False)[0]  # never past 99%


def test_key_status_never_persists_identity_fields(key, router):
    status(router, monthly=12)
    st = run(elfa.refresh_status(force=True))
    raw = db.q("SELECT v FROM kv WHERE k='elfa:status'")[0]["v"]
    assert st["monthly"] == 12 and st["tier"] == "free"
    assert "SHOULD-NOT-BE-STORED" not in raw and "elfak_" not in raw


def test_trending_job_parses_records_spend_and_persists(key, router):
    status(router)
    router.add("GET", "/v2/aggregations/trending-tokens", {"success": True, "data": {"data": [
        {"token": "btc", "current_count": 600, "previous_count": 1200, "change_percent": -50},
        {"token": "$sol", "current_count": 170, "previous_count": 100, "change_percent": 70}]}})
    rows = run(elfa._job_trending())
    assert [r["sym"] for r in rows] == ["BTC", "SOL"]
    assert elfa.trending()["rows"][1]["chg"] == 70
    assert db.q("SELECT units FROM api_usage WHERE api='elfa'")[0]["units"] == 1
    x = elfa.x_attention("SOL")
    assert x["listed"] and x["rank"] == 2 and x["mentions"] == 170
    f = elfa.features("SOL")
    assert f["x_listed"] == 1.0 and 0 < f["x_mentions"] < 1 and f["x_chg"] == pytest.approx(0.7)
    assert elfa.features("DOGE")["x_listed"] == 0.0


def test_run_due_skips_fresh_jobs(key, router):
    status(router)
    for name in elfa.JOBS:
        elfa.kv_put(f"elfa:{name}", {"rows": []})  # all just ran
    ran = run(elfa.run_due())
    assert ran == []
    assert not [c for c in router.calls if "/v2/" in str(c.url) and "key-status" not in str(c.url)]


def test_plan_error_is_raised_and_recorded(key, router):
    status(router)
    router.add("GET", "/v2/data/trending-narratives", httpx.Response(403, json={"message": "requires Grow plan"}))
    with pytest.raises(elfa.PlanError):
        run(elfa._job_narratives())
    assert not db.q("SELECT units FROM api_usage WHERE api='elfa'")  # 403 costs nothing


def test_top_mentions_cache_means_no_second_spend(key, router):
    status(router)
    router.add("GET", "/v2/data/top-mentions", {"success": True, "data": [
        {"link": "https://x.com/a/status/1", "viewCount": 10, "likeCount": 1, "account": {"username": "a"},
         "repostBreakdown": {"smart": 2, "ct": 1}}]})
    a = run(elfa.top_mentions("eth"))
    b = run(elfa.top_mentions("ETH"))
    assert a["rows"][0]["smart_reposts"] == 2 and b["cached"]
    assert len([c for c in router.calls if "top-mentions" in str(c.url)]) == 1


def test_chat_unavailable_on_free_tier(key):
    elfa.kv_put("elfa:status", {"tier": "free", "monthly": 0, "local_since": 0})
    ok, why = elfa.chat_available()
    assert not ok and "Grow" in why
    with pytest.raises(elfa.PlanError):
        async def drain():
            async for _ in elfa.chat_stream("hi"):
                pass
        run(drain())


def test_sse_parser_maps_events():
    lines = [": keep-alive", 'data: {"type":"session_info","sessionId":"s1"}', 'data: {"type":"status","message":"Searching"}',
             'data: {"type":"text","content":"Hello "}', 'data: {"type":"text","content":"world"}',
             'data: {"type":"complete","creditsConsumed":0}', "data: [DONE]", 'data: {"type":"text","content":"ignored"}']

    async def gen():
        for ln in lines:
            yield ln

    async def collect():
        return [e async for e in elfa._parse_sse(gen(), "thread-1")]
    evs = run(collect())
    assert [e["type"] for e in evs] == ["status", "delta", "delta"]
    assert "".join(e["text"] for e in evs if e["type"] == "delta") == "Hello world"
    assert elfa._sessions["thread-1"] == "s1"


def test_sse_parser_raises_on_error():
    async def gen():
        yield 'data: {"type":"error","message":"boom"}'

    async def collect():
        return [e async for e in elfa._parse_sse(gen())]
    with pytest.raises(RuntimeError, match="boom"):
        run(collect())


def test_no_key_means_disabled():
    assert not elfa.enabled()
    assert run(elfa.run_due()) == []
    assert json.dumps(elfa.status())  # serialisable for the API
