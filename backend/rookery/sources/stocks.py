"""Stocks, indices, FX, rates, CNN Fear & Greed. Yahoo (crumb session) -> brapi (B3) -> Stooq; SEC for fundamentals."""
import asyncio
import re
import time
import xml.etree.ElementTree as ET

import httpx

from .. import net
from ..config import secret
from ..net import UA_BARE, UA_BROWSER, UA_SEC, cached, chain, get_json, get_text


# ------------------------------------------------------------------ Yahoo session (cookie + crumb)
class Yahoo:
    def __init__(self):
        self.c = None
        self.crumb = None
        self.crumb_ts = 0
        self.lock = asyncio.Lock()
        self.host = 0

    async def _ensure(self, force=False):
        async with self.lock:
            if self.c is None:
                self.c = httpx.AsyncClient(headers={"User-Agent": UA_BARE}, follow_redirects=True, timeout=12)
            if force or not self.crumb or time.time() - self.crumb_ts > 6 * 3600:
                try:
                    await self.c.get("https://fc.yahoo.com")
                except httpx.HTTPError:
                    pass
                r = await self.c.get("https://query2.finance.yahoo.com/v1/test/getcrumb")
                if r.status_code != 200 or not r.text or "<" in r.text:
                    raise net.SourceError(f"yahoo crumb HTTP {r.status_code}")
                self.crumb, self.crumb_ts = r.text.strip(), time.time()

    async def get(self, path, params=None, crumb=True):
        if crumb:
            await self._ensure()
        assert self.c is not None
        last = None
        for attempt in range(2):
            for host in ("query1", "query2") if self.host == 0 else ("query2", "query1"):
                p = dict(params or {})
                if crumb:
                    p["crumb"] = self.crumb
                r = await self.c.get(f"https://{host}.finance.yahoo.com{path}", params=p)
                if r.status_code == 200:
                    return r.json()
                last = r.status_code
                if r.status_code == 429:
                    self.host ^= 1
                    await asyncio.sleep(0.4)
                if r.status_code in (401, 403) and crumb and attempt == 0:
                    await self._ensure(force=True)
                    break
        raise net.SourceError(f"yahoo HTTP {last} {path}")


YH = Yahoo()

_YH_IV = {"1m": ("1m", "1d"), "5m": ("5m", "5d"), "15m": ("15m", "1mo"), "30m": ("30m", "1mo"),
          "1h": ("60m", "3mo"), "4h": ("60m", "1y"), "1d": ("1d", "5y"), "1w": ("1wk", "10y")}


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# ------------------------------------------------------------------ quotes
def _yq(q):
    return {"sym": q["symbol"], "name": q.get("shortName") or q.get("longName"), "price": q.get("regularMarketPrice"),
            "chg": q.get("regularMarketChangePercent"), "prev": q.get("regularMarketPreviousClose"),
            "high": q.get("regularMarketDayHigh"), "low": q.get("regularMarketDayLow"),
            "vol": q.get("regularMarketVolume"), "mcap": q.get("marketCap"), "currency": q.get("currency"),
            "state": q.get("marketState"), "pe": q.get("trailingPE"), "fpe": q.get("forwardPE"),
            "hi52": q.get("fiftyTwoWeekHigh"), "lo52": q.get("fiftyTwoWeekLow"),
            "post": q.get("postMarketPrice"), "post_chg": q.get("postMarketChangePercent"),
            "pre": q.get("preMarketPrice"), "pre_chg": q.get("preMarketChangePercent"),
            "time": q.get("regularMarketTime"), "type": q.get("quoteType")}


async def quotes(syms):
    syms = [s for s in dict.fromkeys(syms) if s]
    if not syms:
        return []

    async def yahoo():
        d = await YH.get("/v7/finance/quote", {"symbols": ",".join(syms)})
        return [_yq(q) for q in d["quoteResponse"]["result"]]

    async def yahoo_chart():
        out = []
        for s in syms:
            try:
                d = await YH.get(f"/v8/finance/chart/{s}", {"range": "1d", "interval": "1d"}, crumb=False)
                m = d["chart"]["result"][0]["meta"]
                p, pc = m.get("regularMarketPrice"), m.get("chartPreviousClose") or m.get("previousClose")
                out.append({"sym": s, "name": m.get("shortName") or m.get("longName"), "price": p,
                            "chg": (p / pc - 1) * 100 if p and pc else None, "prev": pc,
                            "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
                            "vol": m.get("regularMarketVolume"), "currency": m.get("currency"),
                            "hi52": m.get("fiftyTwoWeekHigh"), "lo52": m.get("fiftyTwoWeekLow"),
                            "time": m.get("regularMarketTime")})
            except Exception:  # noqa: BLE001
                continue
            await asyncio.sleep(0.12)
        return out

    async def brapi():
        b3 = [s[:-3] for s in syms if s.endswith(".SA")]
        if not b3:
            raise net.SourceError("no B3 symbols")
        tok = secret("BRAPI_TOKEN")
        out = []
        for s in b3:
            d = await get_json(f"https://brapi.dev/api/quote/{s}", params={"token": tok} if tok else None)
            for q in d.get("results", []):
                out.append({"sym": q["symbol"] + ".SA", "name": q.get("longName"), "price": q.get("regularMarketPrice"),
                            "chg": q.get("regularMarketChangePercent"), "prev": q.get("regularMarketPreviousClose"),
                            "high": q.get("regularMarketDayHigh"), "low": q.get("regularMarketDayLow"),
                            "vol": q.get("regularMarketVolume"), "mcap": q.get("marketCap"), "currency": "BRL"})
        return out

    async def fetch():
        rows, src = await chain("quotes", [("yahoo", yahoo), ("yahoo-chart", yahoo_chart), ("brapi", brapi)],
                                cooldown_s=45)
        for r in rows:
            r["source"] = src
        got = {r["sym"] for r in rows}
        missing = [s for s in syms if s not in got and s.endswith(".SA")]
        if missing and src != "brapi":
            try:
                extra = await brapi()
                rows += [r | {"source": "brapi"} for r in extra if r["sym"] in missing]
            except Exception:  # noqa: BLE001
                pass
        return rows
    return await cached("q:" + ",".join(syms), 20, fetch)


# ------------------------------------------------------------------ klines
async def klines(sym, interval="1d"):
    if interval not in _YH_IV:
        interval = "1d"
    yiv, rng = _YH_IV[interval]

    async def yahoo():
        d = await YH.get(f"/v8/finance/chart/{sym}", {"range": rng, "interval": yiv, "includePrePost": "false"},
                         crumb=False)
        r = d["chart"]["result"][0]
        ts = r.get("timestamp") or []
        q = r["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
            if None in (o, h, l, c):
                continue
            rows.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": v or 0})
        if interval == "4h":
            rows = _resample(rows, 4 * 3600)
        return rows

    async def stooq():
        if interval not in ("1d", "1w"):
            raise net.SourceError("stooq daily only")
        s = _stooq_sym(sym)
        txt = await get_text(f"https://stooq.com/q/d/l/?s={s}&i={'d' if interval == '1d' else 'w'}", ua=UA_BARE)
        if not txt.startswith("Date"):
            raise net.SourceError("stooq bot wall")
        rows = []
        for line in txt.strip().splitlines()[1:]:
            p = line.split(",")
            if len(p) < 5:
                continue
            t = int(time.mktime(time.strptime(p[0], "%Y-%m-%d")))
            rows.append({"t": t, "o": float(p[1]), "h": float(p[2]), "l": float(p[3]), "c": float(p[4]),
                         "v": float(p[5]) if len(p) > 5 and p[5] else 0})
        return rows[-1500:]

    async def brapi():
        if not sym.endswith(".SA"):
            raise net.SourceError("not B3")
        tok = secret("BRAPI_TOKEN")
        p = {"range": "1y" if interval in ("1d", "1w") else "5d", "interval": "1d" if interval in ("1d", "1w") else "1h"}
        if tok:
            p["token"] = tok
        d = await get_json(f"https://brapi.dev/api/quote/{sym[:-3]}", params=p)
        h = d["results"][0].get("historicalDataPrice") or []
        return [{"t": int(x["date"]), "o": x["open"], "h": x["high"], "l": x["low"], "c": x["close"],
                 "v": x.get("volume") or 0} for x in h if x.get("close") is not None]

    async def fetch():
        rows, src = await chain(f"stock-klines:{sym}", [("yahoo", yahoo), ("brapi", brapi), ("stooq", stooq)],
                                cooldown_s=30)
        return {"sym": sym, "interval": interval, "source": src, "rows": rows}
    return await cached(f"skl:{sym}:{interval}", 30 if interval in ("1m", "5m") else 120, fetch)


def _resample(rows, sec):
    out = []
    for r in rows:
        b = r["t"] - r["t"] % sec
        if out and out[-1]["t"] == b:
            o = out[-1]
            o["h"], o["l"], o["c"], o["v"] = max(o["h"], r["h"]), min(o["l"], r["l"]), r["c"], o["v"] + r["v"]
        else:
            out.append({**r, "t": b})
    return out


_STOOQ = {"^GSPC": "^spx", "^NDX": "^ndx", "^DJI": "^dji", "DX-Y.NYB": "dx.f", "GC=F": "gc.f", "CL=F": "cl.f",
          "BRL=X": "usdbrl", "^TNX": "10usy.b", "^BVSP": "^bvp"}


def _stooq_sym(sym):
    if sym in _STOOQ:
        return _STOOQ[sym]
    if sym.endswith(".SA"):
        return sym[:-3].lower() + ".br"
    return sym.lower().replace(".", "-") + ".us"


# ------------------------------------------------------------------ fundamentals
async def fundamentals(sym):
    async def yahoo():
        d = await YH.get(f"/v10/finance/quoteSummary/{sym}", {
            "modules": "assetProfile,summaryDetail,defaultKeyStatistics,financialData,calendarEvents,"
                       "recommendationTrend,earningsTrend"})
        r = d["quoteSummary"]["result"][0]
        raw = lambda mod, k: ((r.get(mod) or {}).get(k) or {}).get("raw") if isinstance(  # noqa: E731
            (r.get(mod) or {}).get(k), dict) else (r.get(mod) or {}).get(k)
        ap = r.get("assetProfile") or {}
        rec = ((r.get("recommendationTrend") or {}).get("trend") or [{}])[0]
        earn = ((r.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
        return {
            "name": ap.get("longName"), "sector": ap.get("sector"), "industry": ap.get("industry"),
            "country": ap.get("country"), "employees": ap.get("fullTimeEmployees"),
            "summary": (ap.get("longBusinessSummary") or "")[:1200], "website": ap.get("website"),
            "mcap": raw("summaryDetail", "marketCap"), "pe": raw("summaryDetail", "trailingPE"),
            "fpe": raw("summaryDetail", "forwardPE"), "ps": raw("summaryDetail", "priceToSalesTrailing12Months"),
            "pb": raw("defaultKeyStatistics", "priceToBook"), "peg": raw("defaultKeyStatistics", "pegRatio"),
            "ev_ebitda": raw("defaultKeyStatistics", "enterpriseToEbitda"),
            "div_yield": raw("summaryDetail", "dividendYield"), "beta": raw("summaryDetail", "beta"),
            "revenue": raw("financialData", "totalRevenue"), "rev_growth": raw("financialData", "revenueGrowth"),
            "earn_growth": raw("financialData", "earningsGrowth"),
            "gross_margin": raw("financialData", "grossMargins"), "op_margin": raw("financialData", "operatingMargins"),
            "net_margin": raw("financialData", "profitMargins"), "roe": raw("financialData", "returnOnEquity"),
            "fcf": raw("financialData", "freeCashflow"), "cash": raw("financialData", "totalCash"),
            "debt": raw("financialData", "totalDebt"), "de": raw("financialData", "debtToEquity"),
            "current_ratio": raw("financialData", "currentRatio"),
            "target_mean": raw("financialData", "targetMeanPrice"), "target_hi": raw("financialData", "targetHighPrice"),
            "target_lo": raw("financialData", "targetLowPrice"), "rec": raw("financialData", "recommendationKey"),
            "analysts": raw("financialData", "numberOfAnalystOpinions"),
            "rec_trend": {k: rec.get(k) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")},
            "short_pct": raw("defaultKeyStatistics", "shortPercentOfFloat"),
            "next_earnings": earn[0].get("fmt") if earn else None,
        }

    async def sec():
        if "." in sym or sym.startswith("^"):
            raise net.SourceError("SEC covers US tickers only")
        return await sec_fundamentals(sym)

    async def fetch():
        data, src = await chain(f"fundamentals:{sym}", [("yahoo", yahoo), ("sec-edgar", sec)], cooldown_s=60)
        return {"sym": sym, "source": src, **data}
    return await cached(f"fund:{sym}", 3600, fetch)


_SEC_FLOW = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"], "op_income": ["OperatingIncomeLoss"], "gross_profit": ["GrossProfit"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"], "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
_SEC_STOCK = {"cash": ["CashAndCashEquivalentsAtCarryingValue"], "debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
              "equity": ["StockholdersEquity"]}


async def sec_fundamentals(sym):
    tick = await cached("sec:tickers", 86400, lambda: get_json("https://www.sec.gov/files/company_tickers.json",
                                                                 ua=UA_SEC))
    cik = next((v["cik_str"] for v in tick.values() if v["ticker"].upper() == sym.upper()), None)
    if not cik:
        raise net.SourceError("unknown ticker at SEC")
    facts = await cached(f"sec:facts:{cik}", 86400, lambda: get_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json", ua=UA_SEC, timeout=30))
    gaap = facts["facts"].get("us-gaap", {})

    def series(names, annual):
        pat = re.compile(r"^CY\d{4}$" if annual else r"^CY\d{4}Q\d$")
        pts = {}
        for n in reversed(names):  # earlier names win on overlap; companies switch concepts over time
            units = (gaap.get(n) or {}).get("units", {}).get("USD") or []
            pts.update({u["frame"]: u["val"] for u in units if u.get("frame") and pat.match(u["frame"])})
        return sorted(pts.items())

    def instant(names):
        for n in names:
            units = (gaap.get(n) or {}).get("units", {}).get("USD")
            if units:
                u = max(units, key=lambda u: u.get("end", ""))
                return u["val"]
        return None

    out = {"name": facts.get("entityName")}
    annual = {k: series(v, True)[-4:] for k, v in _SEC_FLOW.items()}
    out["annual"] = annual
    # align every flow metric to the latest revenue year so margins compare like with like
    ref = annual["revenue"][-1][0] if annual["revenue"] else None
    out["fiscal_frame"] = ref
    for k in _SEC_FLOW:
        a = dict(annual[k])
        out[k] = a.get(ref) if ref else (annual[k][-1][1] if annual[k] else None)
    rv = annual["revenue"]
    if len(rv) >= 2 and rv[-2][1]:
        out["rev_growth"] = rv[-1][1] / rv[-2][1] - 1
    if out.get("revenue"):
        for k, nk in (("net_income", "net_margin"), ("op_income", "op_margin"), ("gross_profit", "gross_margin")):
            if out.get(k) is not None:
                out[nk] = out[k] / out["revenue"]
    if out.get("ocf") is not None and out.get("capex") is not None:
        out["fcf"] = out["ocf"] - out["capex"]
    for k, v in _SEC_STOCK.items():
        out[k] = instant(v)
    sh = ((facts["facts"].get("dei") or {}).get("EntityCommonStockSharesOutstanding") or {}).get("units", {}).get("shares")
    if sh:
        out["shares"] = max(sh, key=lambda u: u.get("end", ""))["val"]
    return out


# ------------------------------------------------------------------ macro
async def treasury_curve():
    async def fetch():
        ym = time.strftime("%Y%m")
        xml = await get_text("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml",
                             params={"data": "daily_treasury_yield_curve", "field_tdr_date_value_month": ym},
                             ua=UA_BROWSER)
        if "<entry" not in xml:  # early in the month: use last month
            prev = time.strftime("%Y%m", time.localtime(time.time() - 20 * 86400))
            xml = await get_text("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml",
                                 params={"data": "daily_treasury_yield_curve", "field_tdr_date_value_month": prev},
                                 ua=UA_BROWSER)
        ns = {"a": "http://www.w3.org/2005/Atom",
              "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
              "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
        root = ET.fromstring(xml)
        rows = []

        def val(props, k):
            el = props.find(f"d:{k}", ns)
            return None if el is None else el.text

        for e in root.findall("a:entry", ns):
            p = e.find("a:content/m:properties", ns)
            if p is None:
                continue
            rows.append({"date": (val(p, "NEW_DATE") or "")[:10], "3m": _f(val(p, "BC_3MONTH")),
                         "2y": _f(val(p, "BC_2YEAR")), "5y": _f(val(p, "BC_5YEAR")),
                         "10y": _f(val(p, "BC_10YEAR")), "30y": _f(val(p, "BC_30YEAR"))})
        rows.sort(key=lambda r: r["date"])
        if not rows:
            raise net.SourceError("no treasury rows")
        last = rows[-1]
        if last["10y"] is not None and last["2y"] is not None:
            last["spread_2s10s"] = round(last["10y"] - last["2y"], 3)
        net.HEALTH.ok("rates", "us-treasury", 0)
        return last
    try:
        return await cached("treasury", 3600, fetch)
    except Exception as e:  # noqa: BLE001
        net.HEALTH.fail("rates", "us-treasury", e)
        return None


async def cnn_fear_greed():
    async def fetch():
        d = await get_json("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", ua=UA_BROWSER,
                           headers={"Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com",
                                    "Accept": "application/json"})
        fg = d["fear_and_greed"]
        hist = [{"t": int(x["x"] // 1000), "v": x["y"]} for x in d["fear_and_greed_historical"]["data"][-180:]]
        net.HEALTH.ok("stock-fear-greed", "cnn", 0)
        return {"value": round(fg["score"]), "label": fg["rating"], "prev_week": fg.get("previous_1_week"),
                "prev_month": fg.get("previous_1_month"), "series": hist}
    try:
        return await cached("cnn_fg", 900, fetch)
    except Exception as e:  # noqa: BLE001
        net.HEALTH.fail("stock-fear-greed", "cnn", e)
        return None


async def usdbrl():
    async def awesome():
        d = await get_json("https://economia.awesomeapi.com.br/json/last/USD-BRL")
        x = d["USDBRL"]
        return {"rate": float(x["bid"]), "chg": float(x["pctChange"])}

    async def frank():
        d = await get_json("https://api.frankfurter.dev/v1/latest", params={"from": "USD", "to": "BRL"})
        return {"rate": d["rates"]["BRL"], "chg": None}

    async def fetch():
        v, src = await chain("usdbrl", [("awesomeapi", awesome), ("frankfurter", frank)], cooldown_s=60)
        return {**v, "source": src}
    return await cached("usdbrl", 60, fetch)


async def macro(items):
    syms = [m["sym"] for m in items]
    res = await asyncio.gather(quotes(syms), treasury_curve(), cnn_fear_greed(), usdbrl(), return_exceptions=True)
    q, curve, cnn, brl = [None if isinstance(r, BaseException) else r for r in res]
    by = {r["sym"]: r for r in (q if isinstance(q, list) else [])}
    rows = []
    for m in items:
        r = by.get(m["sym"])
        rows.append({"sym": m["sym"], "label": m["label"], "price": r and r.get("price"), "chg": r and r.get("chg"),
                     "state": r and r.get("state"), "source": r and r.get("source")})
    return {"quotes": rows, "rates": curve, "cnn_fg": cnn, "usdbrl": brl}
