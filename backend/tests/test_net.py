"""Latency-ranked fallback chains, cache de-dupe, stale serving."""
import asyncio

import pytest

from rookery import net
from tests.conftest import run


def test_order_prefers_fast_sources_with_hysteresis():
    for _ in range(5):
        net.HEALTH.ok("klines:BTC", "slow", 400)
        net.HEALTH.ok("klines:BTC", "fast", 80)
        net.HEALTH.ok("klines:BTC", "close", 360)  # within 25% of "slow": static order decides
    assert net.order(["slow", "close", "fast"], "klines") == ["fast", "slow", "close"]
    assert net.order(["close", "slow", "fast"], "klines") == ["fast", "close", "slow"]


def test_hysteresis_has_no_bucket_edges():
    # 100 vs 110 straddle a log-1.25 bucket edge; real hysteresis must still keep static order
    assert net.hysteresis_rank(["a", "b"], {"a": 110, "b": 100}) == ["a", "b"]
    assert net.hysteresis_rank(["a", "b"], {"a": 124, "b": 100}) == ["a", "b"]
    assert net.hysteresis_rank(["a", "b"], {"a": 126, "b": 100}) == ["b", "a"]
    assert net.hysteresis_rank(["a", "b", "c"], {"a": 500, "b": 90, "c": 100}) == ["b", "c", "a"]


def test_failures_penalise_cost():
    for _ in range(5):
        net.HEALTH.ok("x:1", "a", 100)
        net.HEALTH.ok("x:1", "b", 150)
    assert net.order(["b", "a"], "x") == ["a", "b"]
    for _ in range(4):
        net.HEALTH.fail("x:1", "a", "boom")
    assert net.order(["b", "a"], "x") == ["b", "a"]


def test_unmeasured_sources_sit_at_the_median():
    for _ in range(3):
        net.HEALTH.ok("y:1", "fast", 50)
        net.HEALTH.ok("y:1", "slow", 900)
    assert net.order(["new", "slow", "fast"], "y")[0] == "fast"
    assert net.order(["new", "slow", "fast"], "y")[-1] == "slow"


def test_adaptive_off_keeps_static_order():
    net.HEALTH.ok("z:1", "b", 10)
    net.ADAPTIVE["on"] = False
    assert net.order(["a", "b"], "z") == ["a", "b"]


def test_chain_falls_back_and_records_health():
    async def bad():
        raise net.SourceError("down")

    async def good():
        return [1, 2]

    data, src = run(net.chain("t:1", [("a", bad), ("b", good)], adaptive=False))
    assert (data, src) == ([1, 2], "b")
    snap = {s["source"]: s for s in net.HEALTH.snapshot()}
    assert snap["a"]["status"] == "down" and snap["b"]["status"] == "ok"
    # "a" is cooling down: next call goes straight to "b"
    calls = []

    async def bad2():
        calls.append("a")
        raise net.SourceError("down")
    run(net.chain("t:1", [("a", bad2), ("b", good)], adaptive=False))
    assert calls == []


def test_chain_raises_when_all_fail():
    async def bad():
        raise net.SourceError("nope")
    with pytest.raises(net.SourceError):
        run(net.chain("t:2", [("a", bad), ("b", bad)]))


def test_cached_dedupes_concurrent_calls_and_serves_stale():
    n = {"calls": 0}

    async def fetch():
        n["calls"] += 1
        await asyncio.sleep(0.05)
        return n["calls"]

    async def main():
        a, b, c = await asyncio.gather(*(net.cached("k", 60, fetch) for _ in range(3)))
        return a, b, c
    assert run(main()) == (1, 1, 1) and n["calls"] == 1

    async def fail():
        raise RuntimeError("x")
    net._cache["k"] = (0, "stale-value")  # expired entry
    assert run(net.cached("k", 60, fail)) == "stale-value"
