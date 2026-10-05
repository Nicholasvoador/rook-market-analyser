"""Cross-asset boards for the Markets page: crypto, stocks, commodities, indices, FX, rates, ETFs.

History comes from Yahoo spark (1y of daily closes, 20 symbols per request) or, for crypto, from exchange daily
candles; live quotes go on top. Brazil data comes from BCB SGS (CDI, Selic, IPCA), so every return can be shown
in BRL and against CDI, the real hurdle rate for a BRL-based investor. All metrics are computed the same way for
every asset class, so rows are comparable across tabs.
"""
import asyncio
import bisect
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np

from .. import indicators as ind
from .. import net
from ..config import load_settings
from ..net import cached, chain, get_json
from . import crypto, stocks

UNIVERSE: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "stocks": [
        ("US mega caps", [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("AMZN", "Amazon"),
                          ("GOOGL", "Alphabet"), ("META", "Meta"), ("TSLA", "Tesla"), ("AVGO", "Broadcom"),
                          ("BRK-B", "Berkshire Hathaway"), ("JPM", "JPMorgan"), ("LLY", "Eli Lilly"), ("V", "Visa"),
                          ("XOM", "Exxon Mobil"), ("WMT", "Walmart"), ("NFLX", "Netflix"), ("AMD", "AMD")]),
        ("Crypto-linked", [("COIN", "Coinbase"), ("MSTR", "Strategy"), ("HOOD", "Robinhood"), ("MARA", "MARA Holdings")]),
        ("Brazil (B3)", [("PETR4.SA", "Petrobras"), ("VALE3.SA", "Vale"), ("ITUB4.SA", "Itaú Unibanco"),
                         ("BBDC4.SA", "Bradesco"), ("BBAS3.SA", "Banco do Brasil"), ("B3SA3.SA", "B3"),
                         ("WEGE3.SA", "WEG"), ("ABEV3.SA", "Ambev"), ("RENT3.SA", "Localiza"),
                         ("SUZB3.SA", "Suzano"), ("AXIA3.SA", "Axia (ex-Eletrobras)"), ("PRIO3.SA", "PRIO")]),
    ],
    "commodities": [
        ("Metals", [("GC=F", "Gold"), ("SI=F", "Silver"), ("HG=F", "Copper"), ("PL=F", "Platinum"),
                    ("PA=F", "Palladium")]),
        ("Energy", [("CL=F", "WTI crude oil"), ("BZ=F", "Brent crude oil"), ("NG=F", "Natural gas"),
                    ("RB=F", "Gasoline (RBOB)"), ("HO=F", "Heating oil")]),
        ("Agriculture", [("ZS=F", "Soybeans"), ("ZC=F", "Corn"), ("ZW=F", "Wheat"), ("KC=F", "Coffee"),
                         ("SB=F", "Sugar"), ("CC=F", "Cocoa"), ("CT=F", "Cotton")]),
        ("Livestock", [("LE=F", "Live cattle"), ("HE=F", "Lean hogs")]),
    ],
    "indices": [
        ("Americas", [("^GSPC", "S&P 500"), ("^NDX", "Nasdaq 100"), ("^DJI", "Dow Jones"), ("^RUT", "Russell 2000"),
                      ("^BVSP", "Ibovespa"), ("^MXX", "IPC Mexico")]),
        ("Europe", [("^STOXX50E", "Euro Stoxx 50"), ("^GDAXI", "DAX"), ("^FTSE", "FTSE 100"), ("^FCHI", "CAC 40")]),
        ("Asia", [("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"), ("000001.SS", "Shanghai Composite"),
                  ("^KS11", "KOSPI"), ("^NSEI", "Nifty 50")]),
        ("Volatility", [("^VIX", "VIX (S&P 500 volatility)")]),
    ],
    "fx": [
        ("Dollar", [("DX-Y.NYB", "US Dollar Index"), ("BRL=X", "USD/BRL"), ("EURUSD=X", "EUR/USD"),
                    ("JPY=X", "USD/JPY"), ("GBPUSD=X", "GBP/USD"), ("CNY=X", "USD/CNY"), ("MXN=X", "USD/MXN"),
                    ("CHF=X", "USD/CHF")]),
        ("Real", [("EURBRL=X", "EUR/BRL"), ("GBPBRL=X", "GBP/BRL")]),
    ],
    "rates": [
        ("US Treasury yields", [("^IRX", "13-week T-bill"), ("^FVX", "5-year Treasury"), ("^TNX", "10-year Treasury"),
                                ("^TYX", "30-year Treasury")]),
    ],
    "etfs": [
        ("Broad market", [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("IWM", "Russell 2000"), ("VT", "World stocks"),
                          ("EEM", "Emerging markets"), ("EWZ", "Brazil (MSCI)")]),
        ("Bonds", [("TLT", "20y+ Treasuries"), ("IEF", "7-10y Treasuries"), ("HYG", "US high yield"),
                   ("LQD", "US investment grade")]),
        ("Gold & commodities", [("GLD", "Gold"), ("SLV", "Silver"), ("USO", "Oil"), ("DBC", "Commodity basket")]),
        ("Crypto ETFs", [("IBIT", "iShares Bitcoin"), ("FBTC", "Fidelity Bitcoin"), ("ETHA", "iShares Ethereum")]),
        ("US sectors", [("XLK", "Technology"), ("XLF", "Financials"), ("XLE", "Energy"), ("XLV", "Health care"),
                        ("XLY", "Consumer discretionary"), ("XLP", "Consumer staples"), ("XLI", "Industrials"),
                        ("XLU", "Utilities"), ("XLB", "Materials"), ("XLRE", "Real estate"),
                        ("XLC", "Communication")]),
        ("Listed in Brazil", [("BOVA11.SA", "Ibovespa ETF"), ("IVVB11.SA", "S&P 500 in BRL"),
                              ("HASH11.SA", "Crypto index"), ("GOLD11.SA", "Gold in BRL")]),
    ],
}
TABS = ["crypto", *UNIVERSE]
KIND = {"stocks": "equity", "commodities": "commodity", "indices": "index", "fx": "fx", "rates": "rate", "etfs": "etf"}
INFO: dict[str, tuple[str, str, str]] = {}  # sym -> (label, tab, group)
for _tab, _groups in UNIVERSE.items():
    for _g, _items in _groups:
        for _s, _l in _items:
            INFO.setdefault(_s, (_l, _tab, _g))

STABLE = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "USDS", "TUSD", "PYUSD", "USD1", "BSC-USD", "USDD", "SUSDE", "BUIDL",
          "WBTC", "STETH", "WSTETH", "WEETH", "WETH", "CBBTC", "RETH", "LBTC", "SOLVBTC", "BNSOL", "JITOSOL", "MSOL",
          "EURC", "XAUT", "PAXG", "USDTB", "USDF", "RLUSD"}

# Plain words people use -> Yahoo symbols (used by the AI symbol detector). English + Portuguese.
ALIASES = {
    "GOLD": "GC=F", "OURO": "GC=F", "SILVER": "SI=F", "PRATA": "SI=F", "COPPER": "HG=F", "COBRE": "HG=F",
    "OIL": "CL=F", "CRUDE": "CL=F", "WTI": "CL=F", "BRENT": "BZ=F", "PETRÓLEO": "BZ=F", "PETROLEO": "BZ=F",
    "NATURAL GAS": "NG=F", "NATGAS": "NG=F", "COFFEE": "KC=F", "CAFÉ": "KC=F", "SOYBEANS": "ZS=F", "SOYBEAN": "ZS=F",
    "SOJA": "ZS=F", "CORN": "ZC=F", "MILHO": "ZC=F", "WHEAT": "ZW=F", "COCOA": "CC=F", "SUGAR": "SB=F",
    "S&P 500": "^GSPC", "S&P500": "^GSPC", "S&P": "^GSPC", "SPX": "^GSPC", "NASDAQ": "^NDX", "NDX": "^NDX",
    "DOW JONES": "^DJI", "RUSSELL": "^RUT", "IBOVESPA": "^BVSP", "IBOV": "^BVSP", "NIKKEI": "^N225", "DAX": "^GDAXI",
    "VIX": "^VIX", "DXY": "DX-Y.NYB", "DOLLAR INDEX": "DX-Y.NYB", "DÓLAR": "BRL=X", "DOLAR": "BRL=X",
    "USD/BRL": "BRL=X", "USDBRL": "BRL=X", "10Y": "^TNX", "10-YEAR": "^TNX", "TREASURIES": "^TNX", "TREASURY": "^TNX",
}
_ALIAS_RE = re.compile(r"(?<![A-Z0-9])(" + "|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))
                       + r")(?![A-Z0-9])")


def kind_of(sym: str) -> str:
    if sym in INFO:
        return KIND[INFO[sym][1]]
    if sym.endswith("=F"):
        return "commodity"
    if sym.endswith("=X") or sym == "DX-Y.NYB":
        return "fx"
    if sym.startswith("^"):
        return "index"
    return "equity"  # any other Yahoo symbol; ETFs are recognised from the quote type


def label_of(sym: str):
    return INFO[sym][0] if sym in INFO else None


def is_yahoo_symbol(sym: str) -> bool:
    """True for anything that isn't a plain crypto base symbol (indices, futures, FX, B3, US tickers we know)."""
    return sym in INFO or bool(re.search(r"[=^.]|-[A-Z]$", sym))


def detect_aliases(text: str) -> list[str]:
    out = []
    for m in _ALIAS_RE.finditer(text.upper()):
        s = ALIASES[m.group(1)]
        if s not in out:
            out.append(s)
    return out


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------------ history
SPARK_TTL = 900


async def _spark_batch(batch):
    async def yahoo():
        d = await stocks.YH.get("/v8/finance/spark", {"symbols": ",".join(batch), "range": "1y", "interval": "1d"},
                                crumb=False)
        out = {}
        for k, v in (d or {}).items():
            if not isinstance(v, dict):
                continue
            pts = [(int(t), float(c)) for t, c in zip(v.get("timestamp") or [], v.get("close") or [])
                   if c is not None and math.isfinite(c)]
            if len(pts) >= 5:
                out[k] = pts
        return out
    data, _ = await chain("spark", [("yahoo-spark", yahoo)], cooldown_s=30)
    return data


async def history(syms):
    """{sym: [(ts, close), ...]} with ~1y of daily closes. Cached per symbol, so tabs share fetches."""
    out, need = {}, []
    for s in dict.fromkeys(syms):
        age = net.cache_age(f"spark:{s}")
        if age is not None and age < SPARK_TTL:
            out[s] = net.peek(f"spark:{s}")
        else:
            need.append(s)
    batches = [need[i:i + 20] for i in range(0, len(need), 20)]
    res = await asyncio.gather(*(cached("sparkb:" + ",".join(b), SPARK_TTL, lambda b=b: _spark_batch(b))
                                 for b in batches), return_exceptions=True)
    for b, r in zip(batches, res):
        for s in b:
            if not isinstance(r, BaseException) and s in r:
                net.put(f"spark:{s}", r[s])
                out[s] = r[s]
            elif net.peek(f"spark:{s}"):
                out[s] = net.peek(f"spark:{s}")  # stale beats nothing
    return out


async def crypto_history(bases):
    sem = asyncio.Semaphore(6)

    async def one(b):
        async def fetch():
            async with sem:
                try:
                    k = await crypto.klines(b, "1d", 400)
                except Exception:  # noqa: BLE001  (OKX caps candles at 300 per request)
                    k = await crypto.klines(b, "1d", 300)
            return [(int(r["t"] // 1000 if r["t"] > 1e12 else r["t"]), float(r["c"])) for r in k["rows"]]
        return b, await _safe(cached(f"chist:{b}", SPARK_TTL, fetch))
    return {b: pts for b, pts in await asyncio.gather(*(one(b) for b in bases)) if pts}


# ------------------------------------------------------------------ Brazil (BCB SGS)
async def bcb_series(sid, days):
    async def fetch():
        today = date.today()
        params = {"formato": "json", "dataInicial": (today - timedelta(days=days)).strftime("%d/%m/%Y"),
                  "dataFinal": today.strftime("%d/%m/%Y")}
        try:
            d = await get_json(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{sid}/dados", params=params)
        except Exception:  # noqa: BLE001  BCB intermittently answers with an empty/HTML body: retry once
            await asyncio.sleep(0.8)
            d = await get_json(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{sid}/dados", params=params)
        rows = []
        for r in d or []:
            try:
                rows.append((datetime.strptime(r["data"], "%d/%m/%Y").date(), float(r["valor"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not rows:
            raise net.SourceError(f"BCB series {sid} empty")
        return rows

    async def run():
        rows, _ = await chain("bcb", [("bcb-sgs", fetch)], cooldown_s=120)
        return rows
    return await cached(f"bcb:{sid}:{days}", 6 * 3600, run)


class CDI:
    """Cumulative CDI index from BCB's daily rates, so CDI over any window is one division."""

    def __init__(self, daily):  # [(date, % per day)]
        self.dates, self.idx = [], []
        acc = 1.0
        for d, v in sorted(daily):
            acc *= 1 + v / 100
            self.dates.append(d)
            self.idx.append(acc)

    def at(self, d):
        i = bisect.bisect_right(self.dates, d) - 1
        return 1.0 if i < 0 else self.idx[i]

    def since(self, d):
        """CDI accumulated after date d up to the latest fixing, as a fraction (0.12 = 12%)."""
        if not self.idx or d < self.dates[0] - timedelta(days=5):
            return None
        return self.idx[-1] / self.at(d) - 1


def _latest(rows):
    today = date.today()
    ok = [r for r in rows if r[0] <= today]
    return ok[-1] if ok else None


async def brazil():
    async def build():
        got = await asyncio.gather(bcb_series(12, 800), bcb_series(432, 400), bcb_series(4389, 40),
                                   bcb_series(433, 500), return_exceptions=True)
        cdi_d, selic, cdi_aa, ipca = [[] if isinstance(g, BaseException) else g for g in got]
        if not cdi_d and not selic:
            raise net.SourceError("BCB unavailable")  # not cached: the next call retries
        s_now, c_now = _latest(selic), _latest(cdi_aa)
        ipca12 = (math.prod(1 + v / 100 for _, v in ipca[-12:]) - 1) * 100 if len(ipca) >= 12 else None
        yr = date.today().year
        ipca_ytd = (math.prod(1 + v / 100 for d, v in ipca if d.year == yr) - 1) * 100
        s_1y = next((v for d, v in selic if d >= date.today() - timedelta(days=365)), None)
        cdi = CDI(cdi_d)
        jan1 = date(yr, 1, 1) - timedelta(days=1)
        acc = {k: cdi.since(date.today() - timedelta(days=n)) for k, n in (("m1", 30), ("m3", 91), ("y1", 365))}
        acc["ytd"] = cdi.since(jan1)
        return {
            "selic": s_now[1] if s_now else None, "selic_date": s_now[0].isoformat() if s_now else None,
            "selic_chg_1y_bp": round((s_now[1] - s_1y) * 100) if s_now and s_1y is not None else None,
            "cdi_aa": c_now[1] if c_now else None,
            "cdi_month": ((1 + c_now[1] / 100) ** (1 / 12) - 1) * 100 if c_now else None,
            "ipca12": ipca12, "ipca_ytd": ipca_ytd if ipca else None,
            "ipca_last": {"month": ipca[-1][0].strftime("%Y-%m"), "pct": ipca[-1][1]} if ipca else None,
            "real_rate": ((1 + s_now[1] / 100) / (1 + ipca12 / 100) - 1) * 100 if s_now and ipca12 is not None else None,
            "cdi_acc": {k: (v * 100 if v is not None else None) for k, v in acc.items()},
            "_cdi": cdi if cdi_d else None, "partial": any(isinstance(g, BaseException) for g in got),
        }
    br = await cached("brazil", 3600, build)
    if br.get("partial"):  # some series failed: keep what we have, but retry within a minute
        net._cache["brazil"] = (time.time() - 3600 + 60, br)
    return br


def public_brazil(br):
    return {k: v for k, v in (br or {}).items() if not k.startswith("_")} if br else None


# ------------------------------------------------------------------ metrics
WIN = {"w1": 7, "m1": 30, "m3": 91, "y1": 365}


def _at(pts, ts):
    """Last close at or before ts; None if the history doesn't reach back that far."""
    if not pts or pts[0][0] > ts + 3 * 86400:
        return None
    i = bisect.bisect_right([p[0] for p in pts], ts) - 1
    return pts[max(i, 0)][1]


def _cutoffs(now=None):
    now = now or time.time()
    cut = {k: now - d * 86400 for k, d in WIN.items()}
    cut["ytd"] = datetime(datetime.fromtimestamp(now, timezone.utc).year, 1, 1, tzinfo=timezone.utc).timestamp() - 1
    return cut


def metrics(pts, price=None, *, unit=None, ccy=None, fx=None, fx_now=None, cdi: CDI | None = None, annual=252,
            hi52=None, lo52=None, now=None):
    """Returns over 1W/1M/3M/YTD/1Y (yields: change in basis points), the same in BRL and in excess of CDI when the
    asset is priced in USD or BRL, plus RSI, trend vs EMA50/200, 30d vol, 52-week range position and a sparkline."""
    now = now or time.time()
    if not pts:
        return None
    if price is None:
        price = pts[-1][1]
    closes = [c for _, c in pts]
    if pts[-1][0] > now - 86400:  # today's bar already carries the live price
        closes[-1] = price
    else:
        closes.append(price)
    c = np.array(closes, dtype=float)
    ret, brl, xcdi = {}, {}, {}
    for k, ts in _cutoffs(now).items():
        base = _at(pts, ts)
        if base is None or not base:
            ret[k] = None
            continue
        if unit == "yield":
            ret[k] = (price - base) * 100
            continue
        r = price / base
        ret[k] = (r - 1) * 100
        if ccy in ("USD", "USX") and fx and fx_now:  # USX = US cents (grains): same ratio as USD
            fb = _at(fx, ts)
            r = r * fx_now / fb if fb else None
        elif ccy != "BRL":
            r = None
        if r is not None:
            brl[k] = (r - 1) * 100
            if cdi is not None:
                acc = cdi.since(datetime.fromtimestamp(ts, timezone.utc).date())
                if acc is not None:
                    xcdi[k] = (r / (1 + acc) - 1) * 100
    e50, e200 = ind.last(ind.ema(c, 50)), ind.last(ind.ema(c, 200))
    if e200 is not None and e50 is not None:
        trend = "up" if price > e50 > e200 else "down" if price < e50 < e200 else "mixed"
    elif e50 is not None:
        trend = "up" if price > e50 else "down"
    else:
        trend = None
    lr = np.diff(np.log(c[-31:])) if unit != "yield" else np.diff(c[-31:])
    vol30 = float(np.std(lr, ddof=1) * math.sqrt(annual) * 100) if len(lr) > 10 and unit != "yield" else None
    year = c[-(annual + 1):]
    hi = hi52 if hi52 else float(np.max(year))
    lo = lo52 if lo52 else float(np.min(year))
    hi, lo = max(hi, price), min(lo, price)
    tail = c[-91:]
    step = max(1, math.ceil(len(tail) / 64))
    spark = [round(float(x), 6) for x in tail[::step]]
    if spark[-1] != round(float(tail[-1]), 6):
        spark.append(round(float(tail[-1]), 6))
    return {"price": price, **ret, "brl": brl or None, "xcdi": xcdi or None,
            "rsi": ind.last(ind.rsi(c)), "trend": trend,
            "ema200_dist": (price / e200 - 1) * 100 if e200 else None, "vol30": vol30,
            "hi52": hi, "lo52": lo, "pos52": (price - lo) / (hi - lo) if hi > lo else None,
            "dd52": (price / hi - 1) * 100 if hi else None, "spark": spark}


# ------------------------------------------------------------------ boards
def _row(sym, label, group, tab, q, pts, ctx):
    q = q or {}
    kind = KIND.get(tab) if tab != "crypto" else "crypto"
    if kind == "equity" and q.get("type") == "ETF":
        kind = "etf"
    unit = "yield" if kind == "rate" else None
    ccy = q.get("currency") or ("USD" if tab in ("crypto", "commodities", "rates") else None)
    if kind == "fx":
        ccy = None  # an exchange rate has no "BRL return" of its own
    m = metrics(pts, q.get("price"), unit=unit, ccy=ccy, fx=ctx.get("fx"), fx_now=ctx.get("fx_now"),
                cdi=ctx.get("cdi"), annual=365 if tab == "crypto" else 252, hi52=q.get("hi52"), lo52=q.get("lo52")) \
        if pts else None
    if not m and q.get("price") is None:
        return None
    return {"sym": sym, "label": label or q.get("name") or sym, "name": q.get("name"), "group": group, "tab": tab,
            "kind": kind, "unit": unit, "ccy": q.get("currency") or ccy, "chg1d": q.get("chg"),
            "state": q.get("state"), "mcap": q.get("mcap"), **(m or {"price": q.get("price")})}


async def _ctx():
    hist, br = await asyncio.gather(history(["BRL=X"]), _safe(brazil()))
    fxq = await _safe(stocks.quotes(["BRL=X"]), [])
    fx_now = (fxq[0].get("price") if fxq else None) or (hist.get("BRL=X") or [(0, None)])[-1][1]
    return {"fx": hist.get("BRL=X"), "fx_now": fx_now, "cdi": (br or {}).get("_cdi"), "brazil": br}


async def _board(tab):
    st = load_settings()
    groups = list(UNIVERSE[tab])
    if tab == "stocks":
        mine = [s for s in st["watchlist"]["stocks"] if kind_of(s) == "equity"]
        if mine:
            groups = [("Your watchlist", [(s, label_of(s)) for s in mine])] + groups
    syms = list(dict.fromkeys(s for _, items in groups for s, _ in items))
    quotes, hist, ctx = await asyncio.gather(_safe(stocks.quotes(syms), []), history(syms), _ctx())
    by = {q["sym"]: q for q in quotes}
    out = []
    for g, items in groups:
        rows = [r for r in (_row(s, lab, g, tab, by.get(s), hist.get(s), ctx) for s, lab in items) if r]
        if rows:
            out.append({"name": g, "rows": rows})
    extra = {}
    if tab == "rates":
        br = public_brazil(ctx["brazil"])
        extra = {"brazil": br, "us_curve": await _safe(stocks.treasury_curve())}
    return {"tab": tab, "groups": out, "updated": time.time(), "usdbrl": ctx["fx_now"],
            "cdi_acc": (public_brazil(ctx["brazil"]) or {}).get("cdi_acc"), **extra}


async def _crypto_board():
    st = load_settings()
    mk = await _safe(crypto.markets(80), {"rows": []})
    names = {r["sym"]: r for r in mk["rows"]}
    top = [r["sym"] for r in mk["rows"] if r["sym"] not in STABLE and not r["sym"].startswith("USD")][:30]
    mine = list(st["watchlist"]["crypto"])
    bases = list(dict.fromkeys(mine + top))
    tick = {t["sym"]: t for t in await _safe(crypto.tickers(bases), [])}
    bases = [b for b in bases if b in tick]  # CEX-listed only: live price + candle history
    hist, ctx = await asyncio.gather(crypto_history(bases), _ctx())
    groups = []
    for g, lst in (("Your watchlist", [b for b in mine if b in tick]), ("Top by market cap", [b for b in top if b in tick])):
        rows = []
        for b in lst:
            t, mrow = tick[b], names.get(b, {})
            q = {"price": t.get("last"), "chg": t.get("chg24"), "currency": "USD", "name": mrow.get("name"),
                 "mcap": mrow.get("mcap")}
            r = _row(b, mrow.get("name") or b, g, "crypto", q, hist.get(b), ctx)
            if r:
                r["label"] = mrow.get("name") or b
                rows.append(r)
        if rows:
            groups.append({"name": g, "rows": rows})
    return {"tab": "crypto", "groups": groups, "updated": time.time(), "usdbrl": ctx["fx_now"],
            "cdi_acc": (public_brazil(ctx["brazil"]) or {}).get("cdi_acc"), "source": mk.get("source")}


async def board(tab):
    if tab not in TABS:
        raise ValueError(f"unknown tab {tab}")
    return await cached(f"board:{tab}", 45, _crypto_board if tab == "crypto" else lambda: _board(tab))


async def row(sym, kind="stock"):
    """Metrics for one asset (asset page 'Performance' panel and AI context)."""
    async def build():
        ctx = await _ctx()
        if kind == "crypto":
            b = sym.upper()
            hist = await crypto_history([b])
            t = await _safe(crypto.tickers([b]), [])
            q = {"price": t[0].get("last"), "chg": t[0].get("chg24"), "currency": "USD"} if t else {}
            return _row(b, b, None, "crypto", q, hist.get(b), ctx)
        quotes, hist = await asyncio.gather(_safe(stocks.quotes([sym]), []), history([sym]))
        tab = INFO[sym][1] if sym in INFO else {"commodity": "commodities", "index": "indices", "fx": "fx"}.get(
            kind_of(sym), "stocks")
        return _row(sym, label_of(sym), INFO[sym][2] if sym in INFO else None, tab, quotes[0] if quotes else None,
                    hist.get(sym), ctx)
    return await cached(f"mrow:{kind}:{sym}", 60, build)


def universe():
    return [{"sym": s, "label": lab, "tab": tab, "group": g, "kind": KIND[tab]} for s, (lab, tab, g) in INFO.items()]


# ------------------------------------------------------------------ cross-asset overview
CROSS = [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ether"), ("^GSPC", "S&P 500"), ("^NDX", "Nasdaq 100"), ("GC=F", "Gold"),
         ("CL=F", "WTI oil"), ("DX-Y.NYB", "US dollar (DXY)"), ("^TNX", "US 10y yield"), ("^BVSP", "Ibovespa"),
         ("BRL=X", "USD/BRL")]
PERF = ["BTC-USD", "ETH-USD", "^GSPC", "^NDX", "GC=F", "^BVSP"]
CCY = {"^BVSP": "BRL"}


def _corr(a, b, n):
    x, y = a[-n:], b[-n:]
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < n * 0.7 or np.std(x[m]) == 0 or np.std(y[m]) == 0:
        return None
    return float(np.corrcoef(x[m], y[m])[0, 1])


async def _cross():
    hist, br = await asyncio.gather(history([s for s, _ in CROSS]), _safe(brazil()))
    ser = {s: {datetime.fromtimestamp(t, timezone.utc).date(): c for t, c in pts} for s, pts in hist.items()}
    if "^GSPC" not in ser:
        raise net.SourceError("no S&P history")
    cal = sorted(ser["^GSPC"])

    def ff(s):  # forward-fill onto the US trading calendar
        d = ser.get(s) or {}
        keys = sorted(d)
        out, j, last = [], 0, None
        for day in cal:
            while j < len(keys) and keys[j] <= day:
                last = d[keys[j]]
                j += 1
            out.append(last)
        return out
    al = {s: ff(s) for s, _ in CROSS}
    names = [s for s, _ in CROSS if sum(v is not None for v in al[s]) > len(cal) * 0.8]
    arr = {s: np.array([np.nan if v is None else v for v in al[s]], dtype=float) for s in names}
    R = {s: (np.diff(a) if s == "^TNX" else np.diff(np.log(a))) for s, a in arr.items()}
    matrix = [[(1.0 if a == b else _corr(R[a], R[b], 90)) for b in names] for a in names]
    btc = []
    if "BTC-USD" in R:
        for s in names:
            if s != "BTC-USD":
                btc.append({"sym": s, "label": dict(CROSS)[s], "c30": _corr(R["BTC-USD"], R[s], 30),
                            "c90": _corr(R["BTC-USD"], R[s], 90)})
    cdi = (br or {}).get("_cdi")
    perf = {"dates": [d.isoformat() for d in cal],
            "series": {s: al[s] for s in PERF if s in names},
            "ccy": {s: CCY.get(s, "USD") for s in PERF},
            "labels": {s: dict(CROSS)[s] for s in PERF},
            "usdbrl": al.get("BRL=X"),
            "cdi": [cdi.at(d) for d in cal] if cdi else None}
    summary = {}
    for s in names:  # same metrics() as the boards, so numbers match across the page
        unit = "yield" if s == "^TNX" else None
        m = metrics(hist[s], unit=unit)
        if m:
            summary[s] = {"label": dict(CROSS)[s], "last": m["price"], "unit": unit,
                          **{k: m.get(k) for k in ("w1", "m1", "ytd", "y1")}}
    return {"names": names, "labels": {s: dict(CROSS)[s] for s in names}, "matrix": matrix, "btc": btc,
            "perf": perf, "summary": summary, "readout": _readout(summary, btc, arr, public_brazil(br)),
            "brazil": public_brazil(br), "updated": time.time()}


def _readout(sm, btc, arr, br):
    """Plain-language cross-asset lines: (k, tone, text). Tone is from a risk-asset point of view."""
    L = []

    def pct(x):
        return "n/a" if x is None else f"{x:+.1f}%"
    sp = sm.get("^GSPC")
    if sp:
        a = arr.get("^GSPC")
        e200 = ind.last(ind.ema(a[np.isfinite(a)], 200)) if a is not None else None
        above = e200 is not None and sp["last"] > e200
        L.append(("Stocks", "bull" if (sp["m1"] or 0) > 0 and above else "bear" if (sp["m1"] or 0) < 0 and not above else "neutral",
                  f"S&P 500 {pct(sp['m1'])} over a month, {pct(sp['ytd'])} this year; "
                  + ("above its 200-day average (long-term uptrend)." if above else "below its 200-day average (long-term trend weak).")))
    dx, brl = sm.get("DX-Y.NYB"), sm.get("BRL=X")
    if dx:
        L.append(("Dollar", "bear" if (dx["m1"] or 0) > 1 else "bull" if (dx["m1"] or 0) < -1 else "neutral",
                  f"Dollar index {pct(dx['m1'])} over a month"
                  + (f"; USD/BRL {brl['last']:.2f} ({pct(brl['m1'])})." if brl else ".")
                  + (" A rising dollar is usually a headwind for crypto and emerging markets." if (dx["m1"] or 0) > 1 else "")))
    tn = sm.get("^TNX")
    if tn and tn.get("m1") is not None:
        L.append(("Rates", "bear" if tn["m1"] > 15 else "bull" if tn["m1"] < -15 else "neutral",
                  f"US 10-year yield {tn['last']:.2f}% ({tn['m1']:+.0f} bp in a month)."
                  + (" Rising yields tighten financial conditions." if tn["m1"] > 15 else
                     " Falling yields ease financial conditions." if tn["m1"] < -15 else "")))
    au, oil = sm.get("GC=F"), sm.get("CL=F")
    if au or oil:
        txt = ", ".join(x for x in (f"gold {pct(au['m1'])} over a month ({pct(au['ytd'])} YTD)" if au else None,
                                    f"WTI oil {pct(oil['m1'])}" if oil else None) if x)
        L.append(("Gold & oil", "neutral", txt[:1].upper() + txt[1:] + "."))
    if btc:
        strong = sorted((b for b in btc if b["c90"] is not None and b["sym"] not in ("ETH-USD",)),
                        key=lambda b: -abs(b["c90"]))
        if strong and abs(strong[0]["c90"]) >= 0.25:
            b = strong[0]
            drift = "" if b["c30"] is None else (" and getting stronger" if abs(b["c30"]) > abs(b["c90"]) + 0.1 else
                                                 " but fading lately" if abs(b["c30"]) < abs(b["c90"]) - 0.1 else "")
            L.append(("Bitcoin's driver", "neutral",
                      f"Over 90 days BTC has moved most {'with' if b['c90'] > 0 else 'against'} {b['label']} "
                      f"(correlation {b['c90']:+.2f}{', 30-day ' + format(b['c30'], '+.2f') if b['c30'] is not None else ''})"
                      f"{drift}."))
        elif strong:
            L.append(("Bitcoin's driver", "neutral",
                      "BTC has been trading on its own: no strong link to stocks, gold or the dollar over 90 days."))
    if br and br.get("selic") is not None:
        L.append(("Brazil", "neutral",
                  f"Selic {br['selic']:.2f}%, inflation {br['ipca12']:.1f}% over 12 months, so the real rate is "
                  f"~{br['real_rate']:.1f}%. CDI pays ~{br['cdi_month']:.2f}% a month: the hurdle any BRL investment "
                  f"has to beat for its risk to be worth it." if br.get("ipca12") is not None and br.get("real_rate") is not None
                  else f"Selic {br['selic']:.2f}%."))
    return [{"k": k, "tone": t, "text": x} for k, t, x in L]


async def cross():
    cr = await cached("cross", SPARK_TTL, _cross)
    if not cr.get("brazil") or cr["brazil"].get("partial"):  # don't pin a Brazil-less overview for 15 minutes
        hit = net._cache.get("cross")
        if hit and hit[0] > time.time() - SPARK_TTL + 60:
            net._cache["cross"] = (time.time() - SPARK_TTL + 60, cr)
    return cr


async def movers(n=6):
    """Biggest 1-day moves across stocks, commodities, indices and ETFs."""
    boards = await asyncio.gather(*(_safe(board(t)) for t in ("stocks", "commodities", "indices", "etfs")))
    seen, rows = set(), []
    for b in boards:
        for g in (b or {}).get("groups", []):
            for r in g["rows"]:
                if r["sym"] not in seen and r.get("chg1d") is not None and r["kind"] != "rate" and r["sym"] != "^VIX":
                    seen.add(r["sym"])
                    rows.append({k: r.get(k) for k in ("sym", "label", "kind", "tab", "price", "chg1d", "ccy")})
    rows.sort(key=lambda r: r["chg1d"])
    return {"up": rows[::-1][:n], "down": rows[:n]}


async def context_lines():
    """Compact cross-asset block for the AI analyst."""
    try:
        cr = await asyncio.wait_for(cross(), 8)
    except Exception:  # noqa: BLE001
        return []
    sm = cr["summary"]

    def f(s):
        x = sm[s]
        if x.get("unit") == "yield":
            return (f"{x['label']} {x['last']:.2f}% (1W {x['w1']:+.0f}bp, 1M {x['m1']:+.0f}bp, YTD {x['ytd']:+.0f}bp)"
                    if None not in (x["w1"], x["m1"], x["ytd"]) else f"{x['label']} {x['last']:.2f}%")
        return f"{x['label']} " + ", ".join(f"{k.upper()} {x[k]:+.1f}%" for k in ("w1", "m1", "ytd") if x.get(k) is not None)
    L = ["- Cross-asset (1W / 1M / YTD): " + "; ".join(f(s) for s in sm)]
    if cr["btc"]:
        L.append("- BTC correlation of daily returns (30d / 90d): " + ", ".join(
            f"{b['label']} {b['c30']:+.2f}/{b['c90']:+.2f}" for b in cr["btc"]
            if b["c30"] is not None and b["c90"] is not None))
    br = cr.get("brazil")
    if br and br.get("selic") is not None:
        ca = br.get("cdi_acc") or {}
        L.append(f"- Brazil: Selic {br['selic']:.2f}%, CDI {br.get('cdi_aa')}% a.a. (YTD {ca.get('ytd') or 0:.1f}%, 12m "
                 f"{ca.get('y1') or 0:.1f}%), IPCA 12m {br.get('ipca12') or 0:.2f}%, real rate ~{br.get('real_rate') or 0:.1f}%. "
                 "For a BRL investor, compare returns against CDI.")
    return L
