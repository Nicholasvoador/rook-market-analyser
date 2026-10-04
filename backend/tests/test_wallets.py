"""Solana wallets: address validation, spam flags, safe CEX mapping, privacy of addresses."""
import json

import pytest

from rookery import ai, db, wallets
from rookery.config import SETTINGS_FILE, load_settings, secrets_status, set_secrets, SECRETS_FILE
from rookery.sources import solana
from tests.conftest import run

W = "86xCnPeV69n6t3DnyGvkKobf9FdN2H9oiVDdaMpo2MMY"  # a public address used only as a fixture
SPAM = "Spam1111111111111111111111111111111111111111"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def test_address_validation():
    assert solana.valid_address(W)
    assert solana.valid_address("11111111111111111111111111111111")
    assert not solana.valid_address("0x" + "a" * 40)
    assert not solana.valid_address(W[:-1] + "0")  # '0' is not base58
    assert not solana.valid_address("abc")
    assert solana.mask(W) == "86xC…2MMY"


def rpc_routes(router):
    def rpc(req):
        body = json.loads(req.content)
        if body["method"] == "getBalance":
            return {"jsonrpc": "2.0", "id": 1, "result": {"value": 2_500_000_000}}
        if body["method"] == "getTokenAccountsByOwner":
            prog = body["params"][1]["programId"]
            if prog != solana.TOKEN_PROGRAMS[0]:
                return {"jsonrpc": "2.0", "id": 1, "result": {"value": []}}
            acc = lambda mint, amt: {"account": {"data": {"parsed": {"info": {"mint": mint, "tokenAmount": {"uiAmount": amt}}}}}}
            return {"jsonrpc": "2.0", "id": 1, "result": {"value": [acc(USDC, 120.0), acc(SPAM, 5e8), acc(USDC, 30.0)]}}
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "?"}}
    router.add("POST", "api.mainnet-beta.solana.com", rpc)
    router.add("GET", "/tokens/v2/search", [
        {"id": USDC, "symbol": "USDC", "name": "USD Coin", "isVerified": True, "liquidity": 5e8, "usdPrice": 1.0},
        {"id": SPAM, "symbol": "BTC", "name": "Totally Bitcoin", "isVerified": False, "liquidity": 50, "usdPrice": 0.01},
        {"id": solana.SOL_MINT, "symbol": "SOL", "name": "Wrapped SOL", "isVerified": True, "liquidity": 9e8, "usdPrice": 150.0},
    ])
    router.add("GET", "/price/v3", {USDC: {"usdPrice": 1.0, "priceChange24h": 0.01}, SPAM: {"usdPrice": 0.01},
                                    solana.SOL_MINT: {"usdPrice": 150.0, "priceChange24h": 2.0}})


def test_wallet_balances_merge_accounts_and_price(router):
    rpc_routes(router)
    w = run(solana.wallet(W))
    by = {t["mint"]: t for t in w["tokens"]}
    assert by[solana.SOL_MINT]["amount"] == 2.5 and by[solana.SOL_MINT]["value_usd"] == 375.0
    assert by[USDC]["amount"] == 150.0  # two token accounts merged
    assert w["tokens"][0]["mint"] == SPAM  # nominal $5M of spam sorts first...


def test_spam_is_flagged_and_never_mapped_to_a_cex_ticker(router):
    rpc_routes(router)
    wid = wallets.add(W, "fixture")
    snap = run(wallets.snapshot(db.q("SELECT * FROM wallets WHERE id=?", (wid,))[0]))
    by = {t["mint"]: t for t in snap["tokens"]}
    assert by[SPAM]["flag"] == "unverified" and by[SPAM]["cex"] is None  # a fake "BTC" stays fake
    assert by[USDC]["flag"] == "ok" and by[solana.SOL_MINT]["cex"] == "SOL"
    assert snap["total_ok_usd"] == pytest.approx(375 + 150)  # ...but only real value counts
    assert snap["total_all_usd"] > 1_000_000


def test_illiquid_flag():
    t = {"mint": "x", "price": 1.0, "verified": True, "liquidity": 100_000, "value_usd": 80_000}
    assert wallets.flag(t) == "illiquid"
    t["value_usd"] = 1_000
    assert wallets.flag(t) == "ok"
    assert wallets.flag({**t, "price": None}) == "unpriced"


def test_wallet_add_validation_and_dupes():
    with pytest.raises(ValueError):
        wallets.add("not-an-address")
    wallets.add(W, "a")
    with pytest.raises(ValueError):
        wallets.add(W, "b")
    with pytest.raises(ValueError):
        wallets.add(W, chain="ethereum")


def test_addresses_never_reach_settings_or_ai_context(router, monkeypatch):
    rpc_routes(router)
    wallets.add(W, "My private label")
    assert W not in (SETTINGS_FILE.read_text() if SETTINGS_FILE.exists() else "")

    async def fake_analytics():
        return {"assets": [{"symbol": "SOL", "kind": "crypto", "weight": 70.0, "value_usd": 375.0, "vol": 60.0,
                            "risk_contrib": 80.0, "sources": ["My private label"]}],
                "rows": [{"symbol": "SOL", "wallet": "My private label", "mint": solana.SOL_MINT}],
                "total_usd": 525.0, "pnl_pct": None, "risk": {}, "flags": []}
    from rookery import portfolio
    monkeypatch.setattr(portfolio, "analytics", fake_analytics)

    async def nothing(*a, **k):
        raise RuntimeError("offline")
    from rookery.sources import crypto, sentiment, stocks
    for mod, names in ((crypto, ["global_stats", "tickers", "derivs_universe", "onchain"]),
                       (stocks, ["macro"]), (sentiment, ["crypto_fear_greed"])):
        for n in names:
            monkeypatch.setattr(mod, n, nothing)
    ctx, _ = run(ai.build_context("review my portfolio and wallet"))
    assert "SOL (crypto): 70" in ctx
    assert W not in ctx and "My private label" not in ctx
    assert "375" not in ctx  # amounts stay private unless portfolio_detail=full


def test_secrets_are_write_only():
    set_secrets({"ELFA_API_KEY": "elfak_super_secret"})
    st = secrets_status()
    assert st["ELFA_API_KEY"] == {"label": st["ELFA_API_KEY"]["label"], "set": True}
    assert "elfak_super_secret" not in json.dumps(st) and "elfak_super_secret" not in json.dumps(load_settings())
    assert oct(SECRETS_FILE.stat().st_mode & 0o777) == "0o600"
