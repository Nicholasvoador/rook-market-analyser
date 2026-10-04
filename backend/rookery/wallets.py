"""Tracked wallets (Solana for now). Addresses live in the local DB only.

Each token gets a quality flag so airdropped spam can't inflate the portfolio:
  ok         verified (or SOL) with real liquidity and a price
  unverified not on Jupiter's verified list (most airdrops)
  illiquid   position worth more than half the token's on-chain liquidity, or liquidity < $10k
  unpriced   no price from any source
Only "ok" tokens count toward portfolio totals unless the user opts in per wallet.
"""
import time
from typing import Any

from . import db, net
from .config import load_settings
from .sources import crypto, solana


def list_wallets():
    """Full addresses only go to the local UI (loopback API); the UI shows them masked unless revealed."""
    rows = db.q("SELECT * FROM wallets ORDER BY id")
    for r in rows:
        r["masked"] = solana.mask(r["address"])
        r["include"] = bool(r["include"])
    return rows


def add(address, label="", chain="solana", include=True):
    address = (address or "").strip()
    if chain != "solana":
        raise ValueError("only Solana wallets are supported for now")
    if not solana.valid_address(address):
        raise ValueError("that is not a valid Solana address")
    if db.q("SELECT 1 FROM wallets WHERE address=?", (address,)):
        raise ValueError("wallet already tracked")
    return db.x("INSERT INTO wallets(chain, address, label, include, created) VALUES (?,?,?,?,?)",
                (chain, address, (label or "").strip()[:40] or solana.mask(address), 1 if include else 0, time.time()))


def update(wid, **kw):
    if "label" in kw:
        db.x("UPDATE wallets SET label=? WHERE id=?", (str(kw["label"])[:40], wid))
    if "include" in kw:
        db.x("UPDATE wallets SET include=? WHERE id=?", (1 if kw["include"] else 0, wid))


def delete(wid):
    db.x("DELETE FROM wallets WHERE id=?", (wid,))
    db.x("DELETE FROM kv WHERE k=?", (f"wallet:{wid}",))


def _listed_symbols():
    out = set()
    for venue in ("binance", "bybit", "okx"):
        allt: Any = net.peek(f"tick:{venue}") or {}
        for pair in allt:
            p = pair.replace("-", "")
            if p.endswith("USDT"):
                out.add(p[:-4])
    return out


def flag(t):
    if t["mint"] == solana.SOL_MINT:
        return "ok"
    if not t.get("price"):
        return "unpriced"
    if not t.get("verified"):
        return "unverified"
    liq = t.get("liquidity") or 0
    if liq < 10_000 or (t.get("value_usd") or 0) > liq * 0.5:
        return "illiquid"
    return "ok"


async def snapshot(w, force=False):
    """Balances for one tracked wallet, flagged and mapped to CEX tickers where safe."""
    if force:
        net._cache.pop(f"wallet:{w['address']}", None)
    data = await solana.wallet(w["address"])
    try:
        await crypto.tickers(["BTC"])  # make sure CEX listings are cached for the mapping below
    except Exception:  # noqa: BLE001
        pass
    listed = _listed_symbols()
    dust = load_settings()["solana"].get("hide_dust_usd", 1.0)
    tokens = []
    for t in data["tokens"]:
        t = dict(t)
        t["flag"] = flag(t)
        t["cex"] = solana.cex_symbol(t, listed)
        t["dust"] = (t.get("value_usd") or 0) < dust
        tokens.append(t)
    ok_total = sum(t["value_usd"] or 0 for t in tokens if t["flag"] == "ok")
    out = {"id": w["id"], "label": w["label"], "masked": solana.mask(w["address"]), "include": bool(w["include"]),
           "tokens": tokens, "total_ok_usd": ok_total, "total_all_usd": data["total_usd"],
           "counts": {f: sum(1 for t in tokens if t["flag"] == f) for f in ("ok", "unverified", "illiquid",
                                                                              "unpriced")},
           "ts": data["ts"], "partial": data["partial"]}
    return out


async def positions():
    """Portfolio rows from wallets marked include=1 (only flag=ok, non-dust tokens)."""
    rows = []
    for w in db.q("SELECT * FROM wallets WHERE include=1"):
        try:
            s = await snapshot(w)
        except Exception:  # noqa: BLE001
            continue
        for t in s["tokens"]:
            if t["flag"] != "ok" or t["dust"]:
                continue
            rows.append({"id": f"w{w['id']}:{t['mint'][:6]}", "symbol": t["cex"] or (t["symbol"] or t["mint"][:6]),
                         "kind": "crypto" if t["cex"] else "dex", "qty": t["amount"], "avg_cost": 0,
                         "currency": "USD", "note": "", "source": "wallet", "wallet": w["label"],
                         "mint": t["mint"], "price": t["price"], "chg24": t["chg24"], "icon": t.get("icon")})
    return rows
