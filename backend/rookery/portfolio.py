"""Holdings + risk analytics: allocation, PnL, vol, correlation, drawdown, risk contribution, sizing hints."""
import asyncio
import time
from datetime import datetime, timezone

import numpy as np

from . import db, wallets
from .config import load_settings
from .sources import crypto, solana, stocks


def list_holdings():
    return db.q("SELECT * FROM holdings ORDER BY id")


def add(symbol, kind, qty, avg_cost, currency="USD", note=""):
    return db.x("INSERT INTO holdings(symbol, kind, qty, avg_cost, currency, note, created) VALUES (?,?,?,?,?,?,?)",
                (symbol.upper(), kind, float(qty), float(avg_cost or 0), currency.upper(), note, time.time()))


def update(hid, **kw):
    allowed = {k: v for k, v in kw.items() if k in ("symbol", "kind", "qty", "avg_cost", "currency", "note")}
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)
    db.x(f"UPDATE holdings SET {sets} WHERE id=?", (*allowed.values(), hid))


def delete(hid):
    db.x("DELETE FROM holdings WHERE id=?", (hid,))


def _day(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def _asset_key(r):
    return (r["kind"], r.get("mint") if r["kind"] == "dex" else r["symbol"])


async def _daily(key):
    kind, ident = key
    try:
        if kind == "crypto":
            k = await crypto.klines(ident, "1d", 200)
        elif kind == "dex":
            k = await solana.dex_klines(ident, "1d", 200)
        else:
            k = await stocks.klines(ident, "1d")
        return {_day(r["t"]): r["c"] for r in k["rows"][-200:]}
    except Exception:  # noqa: BLE001
        return {}


async def analytics():
    hs = [{**h, "source": "manual"} for h in list_holdings()]
    wrows = await wallets.positions()
    if not hs and not wrows:
        return {"rows": [], "total_usd": 0, "assets": []}
    st = load_settings()
    cryptos = [h["symbol"] for h in hs if h["kind"] == "crypto"]
    stock_syms = [h["symbol"] for h in hs if h["kind"] not in ("crypto", "dex")]
    tk, qs, brl = await asyncio.gather(crypto.tickers(cryptos) if cryptos else asyncio.sleep(0, []),
                                       stocks.quotes(stock_syms) if stock_syms else asyncio.sleep(0, []),
                                       stocks.usdbrl(), return_exceptions=True)
    tk = {} if isinstance(tk, BaseException) else {t["sym"]: t for t in tk or []}
    qs = {} if isinstance(qs, BaseException) else {q["sym"]: q for q in qs or []}
    fx = brl["rate"] if isinstance(brl, dict) else 5.0
    rows = []
    for h in hs + wrows:
        if h["source"] == "wallet":
            px, chg, ccy = h.get("price"), h.get("chg24"), "USD"
        elif h["kind"] == "crypto":
            t = tk.get(h["symbol"])
            px, chg, ccy = (t["last"], t["chg24"], "USD") if t else (None, None, "USD")
        else:
            q = qs.get(h["symbol"])
            px, chg, ccy = (q["price"], q["chg"], q.get("currency") or "USD") if q else (None, None, "USD")
        to_usd = (1 / fx) if ccy == "BRL" else 1.0
        cost_usd = (h["avg_cost"] or 0) * h["qty"] * ((1 / fx) if h["currency"] == "BRL" else 1.0)
        val = px * h["qty"] * to_usd if px is not None else None
        rows.append({**h, "price": px, "price_ccy": ccy, "chg24": chg, "value_usd": val, "cost_usd": cost_usd,
                     "pnl_usd": (val - cost_usd) if val is not None and cost_usd else None,
                     "pnl_pct": ((val / cost_usd - 1) * 100) if val is not None and cost_usd else None})
    total = sum(r["value_usd"] or 0 for r in rows)
    cost = sum(r["cost_usd"] or 0 for r in rows)
    for r in rows:
        r["weight"] = (r["value_usd"] or 0) / total * 100 if total else 0
    # risk is per ASSET: manual SOL + wallet SOL are one exposure
    assets: dict = {}
    for r in rows:
        a = assets.setdefault(_asset_key(r), {"key": _asset_key(r), "symbol": r["symbol"], "kind": r["kind"],
                                              "value_usd": 0.0, "weight": 0.0, "sources": []})
        a["value_usd"] += r["value_usd"] or 0
        a["weight"] += r["weight"]
        a["sources"].append(r.get("wallet") or "manual")
    alist = sorted(assets.values(), key=lambda a: a["value_usd"], reverse=True)
    series = await asyncio.gather(*(_daily(a["key"]) for a in alist))
    risk = {}
    valid = [i for i, s in enumerate(series) if s]
    days = sorted(set.intersection(*(set(series[i]) for i in valid))) if valid else []
    if len(days) > 30 and valid:
        P = np.array([[series[i][d] for d in days] for i in valid], dtype=float)
        R = np.diff(np.log(P), axis=1)
        vol = R.std(axis=1) * np.sqrt(365) * 100
        w = np.array([alist[i]["weight"] / 100 for i in valid])
        if w.sum() > 0:
            w = w / w.sum()
        cov = np.cov(R) * 365 if len(valid) > 1 else np.array([[R.var() * 365]])
        pv = float(np.sqrt(w @ cov @ w)) * 100
        rc = (w * (cov @ w)) / (pv / 100) ** 2 * 100 if pv else np.zeros_like(w)
        port = (P / P[:, :1] * w[:, None]).sum(axis=0)
        dd = float((port / np.maximum.accumulate(port) - 1).min() * 100)
        inv = 1 / np.maximum(vol, 1e-6)
        ivw = inv / inv.sum() * 100
        corr = np.corrcoef(R).tolist() if len(valid) > 1 else [[1.0]]
        for j, i in enumerate(valid):
            alist[i]["vol"] = float(vol[j])
            alist[i]["risk_contrib"] = float(rc[j])
            alist[i]["inv_vol_weight"] = float(ivw[j])
        risk = {"port_vol": pv, "max_dd": dd, "days": len(days),
                "corr": {"syms": [alist[i]["symbol"] for i in valid], "matrix": corr},
                "target_vol": st["risk"]["target_vol_pct"],
                "vol_scale": st["risk"]["target_vol_pct"] / pv if pv else None}
    by_key = {a["key"]: a for a in alist}
    for r in rows:  # surface per-asset risk on each row for the table
        a = by_key[_asset_key(r)]
        for k in ("vol", "risk_contrib", "inv_vol_weight"):
            if k in a:
                r[k] = a[k]
    flags = []
    for a in alist:
        if a["weight"] > st["risk"]["max_position_pct"]:
            flags.append(f"{a['symbol']} is {a['weight']:.0f}% of the portfolio (limit {st['risk']['max_position_pct']}%)")
        if a.get("risk_contrib") and a["risk_contrib"] > 50 and len(alist) > 1:
            flags.append(f"{a['symbol']} drives {a['risk_contrib']:.0f}% of portfolio risk")
        if a["kind"] == "dex" and a["weight"] > 5:
            flags.append(f"{a['symbol']} is an on-chain-only token at {a['weight']:.0f}%: thin liquidity, exit risk")
    if risk.get("port_vol") and risk["port_vol"] > st["risk"]["target_vol_pct"] * 1.25:
        flags.append(f"Portfolio vol {risk['port_vol']:.0f}% is above your {st['risk']['target_vol_pct']}% target")
    corr = risk.get("corr")
    if corr and len(corr["syms"]) > 1:
        m = np.array(corr["matrix"])
        np.fill_diagonal(m, 0)
        if m.max() > 0.85:
            i, j = np.unravel_index(m.argmax(), m.shape)
            flags.append(f"{corr['syms'][i]} and {corr['syms'][j]} are {m.max():.2f} correlated: little diversification")
    for a in alist:
        a["key"] = list(a["key"])
    return {"rows": rows, "assets": alist, "total_usd": total, "total_brl": total * fx, "cost_usd": cost,
            "pnl_pct": (total / cost - 1) * 100 if cost else None, "usdbrl": fx, "risk": risk, "flags": flags,
            "wallet_value_usd": sum(r["value_usd"] or 0 for r in rows if r["source"] == "wallet")}
