"""Test isolation: every test session gets throwaway config + data dirs, and no test touches the network.

The env vars must be set before `rookery.config` is imported (it creates the dirs at import time).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="rook-test-"))
os.environ["ROOKERY_CONFIG"] = str(_TMP / "config")
os.environ["ROOKERY_DATA"] = str(_TMP / "data")
for k in ("ELFA_API_KEY", "HERMES_API_KEY", "SOLANA_RPC_URL"):
    os.environ.pop(k, None)
os.environ["HERMES_API_URL"] = "http://127.0.0.1:9"  # discard port: health checks fail fast, never reach a real gateway
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from rookery import db, net  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_state():
    """Clean DB, caches, health and settings for every test."""
    from rookery.config import DB_FILE, SECRETS_FILE, SETTINGS_FILE
    for f in (DB_FILE, SETTINGS_FILE, SECRETS_FILE):
        if f.exists():
            f.unlink()
    db.init()
    net._cache.clear()
    net._cooldown.clear()
    net.HEALTH.__init__()
    net.ADAPTIVE["on"] = True
    yield
    net._client = None


def run(coro):
    return asyncio.run(coro)


class Router:
    """httpx.MockTransport with simple (method, url-substring) -> response routing and a call log."""

    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.calls: list[httpx.Request] = []

    def add(self, method, needle, resp):
        self.routes.append((method, needle, resp))
        return self

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        for method, needle, resp in self.routes:
            if request.method == method and needle in str(request.url):
                r = resp(request) if callable(resp) else resp
                if isinstance(r, httpx.Response):
                    return r
                return httpx.Response(200, json=r)
        return httpx.Response(404, json={"error": f"unrouted {request.method} {request.url}"})


@pytest.fixture
def router():
    r = Router()
    net._client = httpx.AsyncClient(transport=httpx.MockTransport(r))
    yield r
    net._client = None


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if anything tries a real request."""
    def boom(request):
        raise AssertionError(f"unexpected network call: {request.url}")
    net._client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    yield
    net._client = None
