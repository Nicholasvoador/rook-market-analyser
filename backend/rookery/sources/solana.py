"""Solana: wallet balances (SOL + SPL + Token-2022), token metadata/prices, DEX-only token charts.

Privacy: wallet addresses live only in the local SQLite DB. They are sent to the RPC node you choose (your own
SOLANA_RPC_URL if set, else public mainnet RPC) because that is how balances are read; they are never sent to the
AI, never logged in full, never written to settings or git.

Sources (latency-ranked fallbacks):
  balances : custom RPC -> api.mainnet-beta.solana.com -> publicnode (SOL balance only; it blocks token scans)
  metadata : Jupiter tokens v2 (symbol, icon, verified, organic score, liquidity, price)
  prices   : Jupiter price v3 -> DexScreener
  candles  : GeckoTerminal OHLCV of the deepest pool
"""
import asyncio
import time

from .. import net
from ..config import secret
from ..net import cached, chain, get_json, post_json

SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
PUBLIC_RPCS = [("solana-mainnet", "https://api.mainnet-beta.solana.com"),
               ("publicnode", "https://solana-rpc.publicnode.com")]
JUP = "https://lite-api.jup.ag"
GT = "https://api.geckoterminal.com/api/v2"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def mask(addr):
    return f"{addr[:4]}…{addr[-4:]}" if addr and len(addr) > 10 else "?"


def valid_address(addr):
    """Base58 that decodes to exactly 32 bytes (an ed25519 public key / program address)."""
    if not addr or not 32 <= len(addr) <= 44 or any(c not in _B58 for c in addr):
        return False
    n = 0
    for c in addr:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(addr) - len(addr.lstrip("1"))
    return len(raw) + pad == 32


# ------------------------------------------------------------------ RPC
def _rpcs(heavy):
    custom = secret("SOLANA_RPC_URL")
    out = [("custom-rpc", custom)] if custom else []
    out += [(n, u) for n, u in PUBLIC_RPCS if not (heavy and n == "publicnode")]
    return out


async def _rpc_call(url, method, params):
    d = await post_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
    if "error" in d:
        raise net.SourceError(f"RPC {d['error'].get('code')}: {str(d['error'].get('message'))[:120]}")
    return d["result"]


async def rpc(method, params, heavy=False):
    attempts = [(name, (lambda u=url: _rpc_call(u, method, params))) for name, url in _rpcs(heavy)]
    res, _ = await chain(f"solrpc:{method}", attempts, cooldown_s=30)
    return res


def rpc_probes():
    """Extra latency probes for the prober (getHealth is ~free)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "getHealth"}
    return {name: ("POST", url, body) for name, url in _rpcs(False)}


# ------------------------------------------------------------------ token metadata + prices
async def token_info(mints):
    """{mint: {symbol, name, icon, decimals, verified, organic, liquidity, mcap, price, chg24}} via Jupiter (cached)."""
    mints = [m for m in dict.fromkeys(mints) if m]
    out, todo = {}, []
    for m in mints:
        hit = net.peek(f"jupinfo:{m}")
        if hit is not None and (net.cache_age(f"jupinfo:{m}") or 1e9) < 600:
            out[m] = hit
        else:
            todo.append(m)
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        try:
            t0 = time.perf_counter()
            d = await get_json(f"{JUP}/tokens/v2/search", params={"query": ",".join(batch)}, timeout=15)
            net.HEALTH.ok("tokeninfo", "jupiter", (time.perf_counter() - t0) * 1000)
        except Exception as e:  # noqa: BLE001
            net.HEALTH.fail("tokeninfo", "jupiter", e)
            continue
        for t in d or []:
            mid = t.get("id")
            if mid not in batch:
                continue
            s24 = t.get("stats24h") or {}
            info = {"mint": mid, "symbol": t.get("symbol"), "name": t.get("name"), "icon": t.get("icon"),
                    "decimals": t.get("decimals"), "verified": bool(t.get("isVerified")),
                    "organic": t.get("organicScoreLabel"), "liquidity": t.get("liquidity"), "mcap": t.get("mcap"),
                    "fdv": t.get("fdv"), "holders": t.get("holderCount"), "price": t.get("usdPrice"),
                    "chg24": s24.get("priceChange"), "vol24": (s24.get("buyVolume") or 0) + (s24.get("sellVolume") or 0),
                    "tags": t.get("tags") or []}
            net.put(f"jupinfo:{mid}", info)
            out[mid] = info
    return out


async def prices(mints):
    """{mint: {price, chg24, source}}: Jupiter price v3 -> DexScreener. Cached 5s (it's polled for live charts)."""
    mints = [m for m in dict.fromkeys(mints) if m]
    key = "solpx:" + ",".join(sorted(mints))

    async def fetch():
        out = {}
        for i in range(0, len(mints), 50):
            batch = mints[i:i + 50]

            async def jup(b=batch):
                d = await get_json(f"{JUP}/price/v3", params={"ids": ",".join(b)}, timeout=8)
                return {m: {"price": v.get("usdPrice"), "chg24": v.get("priceChange24h"), "source": "jupiter"}
                        for m, v in (d or {}).items() if v and v.get("usdPrice")}

            async def dex(b=batch):
                res = {}
                for j in range(0, len(b), 30):
                    d = await get_json(f"https://api.dexscreener.com/tokens/v1/solana/{','.join(b[j:j + 30])}",
                                       timeout=8)
                    best = {}
                    for p in d or []:
                        a = (p.get("baseToken") or {}).get("address")
                        liq = (p.get("liquidity") or {}).get("usd") or 0
                        if a and liq >= best.get(a, (0, None))[0]:
                            best[a] = (liq, p)
                    for a, (_, p) in best.items():
                        res[a] = {"price": float(p.get("priceUsd") or 0) or None,
                                  "chg24": (p.get("priceChange") or {}).get("h24"), "source": "dexscreener"}
                return res
            try:
                got, _ = await chain("solprice", [("jupiter", jup), ("dexscreener", dex)], cooldown_s=20)
                out.update(got)
            except Exception:  # noqa: BLE001
                pass
            missing = [m for m in batch if m not in out]
            if missing and len(missing) < len(batch):  # partial Jupiter answer: fill the gaps from DexScreener
                try:
                    out.update({k: v for k, v in (await dex(missing)).items() if v.get("price")})
                except Exception:  # noqa: BLE001
                    pass
        return out
    return await cached(key, 5, fetch)


# ------------------------------------------------------------------ wallet
async def wallet(address):
    """Balances for one wallet: [{mint, symbol, name, icon, amount, price, value_usd, verified, ...}] by value."""
    if not valid_address(address):
        raise ValueError("not a valid Solana address")

    async def fetch():
        lamports, *accounts = await asyncio.gather(
            rpc("getBalance", [address, {"commitment": "confirmed"}]),
            *(rpc("getTokenAccountsByOwner", [address, {"programId": p}, {"encoding": "jsonParsed",
                                                                         "commitment": "confirmed"}], heavy=True)
              for p in TOKEN_PROGRAMS), return_exceptions=True)
        if isinstance(lamports, BaseException):
            raise lamports
        bal: dict[str, float] = {SOL_MINT: (lamports.get("value") or 0) / 1e9}
        errors = []
        for res in accounts:
            if isinstance(res, BaseException):
                errors.append(str(res)[:120])
                continue
            for a in res.get("value") or []:
                info = (((a.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
                amt = (info.get("tokenAmount") or {}).get("uiAmount") or 0
                if amt and info.get("mint"):
                    bal[info["mint"]] = bal.get(info["mint"], 0) + float(amt)
        mints = list(bal)
        meta, px = await asyncio.gather(token_info(mints), prices(mints))
        rows = []
        for m, amt in bal.items():
            i = meta.get(m) or {}
            p = (px.get(m) or {}).get("price") or i.get("price")
            sym = "SOL" if m == SOL_MINT else (i.get("symbol") or None)
            rows.append({"mint": m, "symbol": sym, "name": "Solana" if m == SOL_MINT else i.get("name"),
                         "icon": i.get("icon"), "amount": amt, "price": p,
                         "value_usd": amt * p if p else None, "chg24": (px.get(m) or {}).get("chg24") or i.get("chg24"),
                         "verified": True if m == SOL_MINT else i.get("verified", False),
                         "liquidity": i.get("liquidity"), "organic": i.get("organic")})
        rows.sort(key=lambda r: r["value_usd"] or 0, reverse=True)
        return {"tokens": rows, "total_usd": sum(r["value_usd"] or 0 for r in rows), "ts": time.time(),
                "partial": bool(errors), "errors": errors}
    return await cached(f"wallet:{address}", 60, fetch)


def cex_symbol(row, listed: set[str]):
    """Map a wallet token to a CEX ticker when it is safe to: verified, liquid and listed (blocks spam look-alikes)."""
    if row["mint"] == SOL_MINT:
        return "SOL"
    sym = (row.get("symbol") or "").upper()
    if sym and row.get("verified") and (row.get("liquidity") or 0) > 1_000_000 and sym in listed:
        return sym
    return None


# ------------------------------------------------------------------ DEX token charts (GeckoTerminal)
_GT_IV = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15), "1h": ("hour", 1), "4h": ("hour", 4),
          "1d": ("day", 1)}


async def top_pool(mint, chain_name="solana"):
    async def fetch():
        d = await get_json(f"{GT}/networks/{chain_name}/tokens/{mint}/pools", params={"page": 1}, timeout=12)
        pools = sorted(d.get("data") or [], key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0),
                       reverse=True)
        if not pools:
            raise net.SourceError("no pools")
        p = pools[0]["attributes"]
        return {"address": p["address"], "name": p.get("name"), "liquidity": float(p.get("reserve_in_usd") or 0),
                "dex": (pools[0].get("relationships") or {}).get("dex", {}).get("data", {}).get("id")}
    return await cached(f"gtpool:{chain_name}:{mint}", 3600, fetch)


async def dex_klines(mint, interval="1h", limit=500, chain_name="solana"):
    if interval not in _GT_IV:
        raise ValueError("bad interval")
    tf, agg = _GT_IV[interval]

    async def fetch():
        pool = await top_pool(mint, chain_name)
        t0 = time.perf_counter()
        d = await get_json(f"{GT}/networks/{chain_name}/pools/{pool['address']}/ohlcv/{tf}",
                           params={"aggregate": agg, "limit": min(limit, 1000), "currency": "usd", "token": mint},
                           timeout=12)
        net.HEALTH.ok("dexklines", "geckoterminal", (time.perf_counter() - t0) * 1000)
        lst = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        rows = [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]),
                 "v": float(k[5])} for k in reversed(lst)]
        return {"sym": mint, "interval": interval, "source": "geckoterminal", "pool": pool, "rows": rows}
    return await cached(f"dexkl:{chain_name}:{mint}:{interval}", 30 if tf == "minute" else 90, fetch)
