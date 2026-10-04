"""AI routing: fallback on failure, Elfa provider gating, prompt built from the local profile."""
import pytest

from rookery import ai
from rookery.config import save_settings
from tests.conftest import run


def test_system_prompt_uses_local_profile_only():
    save_settings({"profile": {"name": "Ana", "timezone": "Europe/Lisbon", "home_currency": "EUR"}})
    p = ai.system_prompt()
    assert "Ana" in p
    assert "Bottom line" in p


def test_plain_style_adds_plain_language_rules():
    save_settings({"ai": {"style": "plain"}})
    assert "plain" in ai.system_prompt().lower()


def test_attempts_order_primary_then_fallback():
    save_settings({"ai": {"primary": {"provider": "openrouter", "model": "x/y", "reasoning_effort": "high"},
                          "fallback": {"provider": "antigravity", "model": "gemini-3.8-flash", "reasoning_effort": "max"}}})
    at, _ = ai._attempts("fast")
    assert [n for n, _ in at] == ["primary", "fallback"]
    assert at[1][1]["model"] == "gemini-3.8-flash" and at[1][1]["reasoning_effort"] == "max"
    exp, _ = ai._attempts("expert")
    assert exp[0][1]["reasoning_effort"] == "max"


def test_elfa_primary_is_skipped_for_utility_mode():
    save_settings({"ai": {"primary": {"provider": "elfa", "model": "elfa-fast"},
                          "fallback": {"provider": "antigravity", "model": "gemini-3.8-flash", "reasoning_effort": "max"}}})
    at, _ = ai._attempts("utility")
    assert [n for n, _ in at] == ["fallback"]


def test_stream_falls_back_and_continues(monkeypatch):
    save_settings({"ai": {"cli_fallback": False, "primary": {"provider": "p", "model": "m1"},
                          "fallback": {"provider": "f", "model": "m2", "reasoning_effort": "max"}}})
    ai._skip_until.clear()
    seen = []

    async def fake_one(spec, messages, **kw):
        seen.append(spec.get("model"))
        if spec.get("model") == "m1":
            yield {"type": "delta", "text": "Bottom line: hold"}
            raise RuntimeError("upstream quota exhausted")
        yield {"type": "delta", "text": " BTC."}

    monkeypatch.setattr(ai, "_stream_spec", lambda spec, msgs, ai_cfg, thread=None: fake_one(spec, msgs))

    async def collect():
        return [e async for e in ai.stream([{"role": "user", "content": "q"}], mode="fast")]
    evs = run(collect())
    text = "".join(e.get("text", "") for e in evs if e["type"] == "delta")
    assert seen == ["m1", "m2"]
    assert text == "Bottom line: hold BTC."
    assert any(e["type"] == "notice" for e in evs)
    assert evs[-1]["type"] == "done" and evs[-1]["content"] == text
    assert any(k.startswith("m1") for k in ai._skip_until)  # circuit breaker armed for the dead primary
    assert not any(k.startswith("m2") for k in ai._skip_until)


def test_all_routes_failing_yields_error(monkeypatch):
    save_settings({"ai": {"cli_fallback": False, "primary": {"provider": "p", "model": "a"},
                          "fallback": {"provider": "f", "model": "b"}}})
    ai._skip_until.clear()

    async def dead(spec, msgs, ai_cfg, thread=None):
        raise RuntimeError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(ai, "_stream_spec", dead)

    async def collect():
        return [e async for e in ai.stream([{"role": "user", "content": "q"}])]
    evs = run(collect())
    assert evs[-1]["type"] == "error" and "All AI routes failed" in evs[-1]["text"]
