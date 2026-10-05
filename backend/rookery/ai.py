"""AI router: Hermes API server (OpenAI-compatible, streaming) with fallback chain + live market context.

primary (Settings model, or Hermes default)  ->  fallback: gemini-3.8-flash @ reasoning max  ->  `hermes chat -Q` CLI
A failure before any text switches model; a failure mid-answer asks the next model to continue from the cut.
"""
import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone

import httpx

from . import db, predict, signals
from .config import load_settings, local_tz, secret, user_name
from .sources import crypto, elfa, markets, sentiment, stocks

SYSTEM_TMPL = """You are **Rook**, {who} personal market analyst inside Rook Market Analyser (crypto first, then stocks and macro). You run on the user's own Hermes models in a local app.

Answer shape (always, in this order):
1. **Bottom line:** one or two sentences that answer the question directly.
2. **Confidence:** low / medium / high, and the single biggest reason why.
3. **Evidence:** tight bullets with the numbers from the LIVE DATA block. Small tables when comparing assets.
4. For trade ideas: **Plan** (bias, entry zone, invalidation/stop, targets, rough R:R, size as % of portfolio within the risk profile).
5. **What would change my mind:** the level or event that flips the view.

Rules:
- Ground every number in the LIVE DATA block or tools you call. Unknown is unknown: never invent prices, flows or news.
- Think in probabilities and scenarios ("if BTC loses X, then..."). Always give an invalidation level for any setup.
- House rules: negative funding alone is NOT a long signal; OI is leverage, not flow; absent data is not neutral; crowd extremes are contrarian context, not triggers; X attention (Elfa) measures attention, not direction.
- The quant models' skill and each setup's historical hit rate are reported honestly in the data. If they say "no edge" or "too few", say so and don't lean on them.
- Respect the risk profile and diversification. Flag concentration, leverage and correlation risk. You can be blunt.
- When you take a directional view on a crypto asset, append ONE fenced block so it gets graded later:
```forecast
[{{"symbol": "BTC", "horizon_h": 24, "p_up": 0.56, "note": "one-line reason"}}]
```
  p_up = probability price is higher after horizon_h hours (4, 24 or 168). Be calibrated: most honest values sit between 0.40 and 0.60.
- Not financial advice: one short line at the end is enough.{style}"""

PLAIN_STYLE = """
- PLAIN LANGUAGE MODE: the reader is not a trader. Avoid jargon; when a term is unavoidable (EMA, RSI, funding, open interest), explain it in a few words the first time. Prefer short sentences and concrete numbers ("about 3% below today's price") over indicator names."""


def system_prompt(st=None):
    st = st or load_settings()
    name = user_name(st)
    style = PLAIN_STYLE if st["ai"].get("style") == "plain" else ""
    return SYSTEM_TMPL.format(who=f"{name}'s" if name else "the user's", style=style)




FAST_HINT = "Mode: FAST. Answer from the live data; only use tools if the question truly needs fresh info that isn't here."
EXPERT_HINT = ("Mode: EXPERT. Be thorough: cross-check the data, use web/search tools for very recent news or "
               "catalysts if relevant, and give a full scenario analysis.")

_KNOWN_STOCKS = re.compile(r"\$?\b([A-Z]{1,5}(?:\.SA)?|[A-Z]{4}\d{1,2}(?:\.SA)?)\b")


# ------------------------------------------------------------------ context
def _fmt(x, d=2, pct=False):
    if x is None:
        return "n/a"
    if pct:
        return f"{x:+.{d}f}%"
    if abs(x) >= 1e12:
        return f"{x / 1e12:.2f}T"
    if abs(x) >= 1e9:
        return f"{x / 1e9:.2f}B"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.2f}M"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 1:
        return f"{x:.{d}f}"
    return f"{x:.6g}"


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return default


def detect_symbols(text, settings):
    up = text.upper()
    found = []
    known_c = set(settings["watchlist"]["crypto"]) | {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK",
                                                       "SUI", "HYPE", "TON", "DOT", "LTC", "TRX", "AAVE", "UNI",
                                                       "PEPE", "ENA", "ONDO", "TAO", "NEAR", "APT", "ARB", "OP"}
    names = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP", "DOGECOIN": "DOGE",
             "CARDANO": "ADA", "HYPERLIQUID": "HYPE", "CHAINLINK": "LINK", "AVALANCHE": "AVAX"}
    for n, s in names.items():
        if n in up and s not in found:
            found.append(s)
    for m in re.finditer(r"\$?\b([A-Z0-9]{2,10})\b", up):
        s = m.group(1)
        if s in known_c and s not in found:
            found.append(s)
    stock_list = set(settings["watchlist"]["stocks"])
    for m in _KNOWN_STOCKS.finditer(text):
        s = m.group(1)
        if (s in stock_list or s + ".SA" in stock_list or m.group(0).startswith("$")) and s not in found \
                and s not in known_c:
            found.append(s + ".SA" if s + ".SA" in stock_list else s)
    for s in markets.detect_aliases(text):  # gold, oil, S&P 500, Ibovespa, dólar, 10y...
        if s not in found:
            found.append(s)
    return found[:6]


async def build_context(user_text, focus=None, include_portfolio=None):
    st = load_settings()
    tz = local_tz(st)
    now = datetime.now(tz)
    syms = list(dict.fromkeys((focus or []) + detect_symbols(user_text, st)))
    crypto_syms = [s for s in syms if not markets.is_yahoo_symbol(s) and not s.isdigit()
                   and s not in st["watchlist"]["stocks"]]
    stock_syms = [s for s in syms if s not in crypto_syms]

    glob, fng, macro, tick, dv, oc, agg_news, liq = await asyncio.gather(
        _safe(crypto.global_stats()), _safe(sentiment.crypto_fear_greed()), _safe(stocks.macro(st["macro"])),
        _safe(crypto.tickers(st["watchlist"]["crypto"]), []), _safe(crypto.derivs_universe()),
        _safe(crypto.onchain()), asyncio.to_thread(sentiment.aggregate), asyncio.to_thread(crypto.LIQS.summary))
    L = [f"## LIVE DATA ({now:%Y-%m-%d %H:%M} {now.tzname()} / {datetime.now(timezone.utc):%H:%M} UTC)"]
    if glob:
        L.append(f"- Crypto mcap {_fmt(glob['mcap'])} ({_fmt(glob.get('mcap_chg24'), pct=True)} 24h), "
                 f"BTC dom {glob['btc_dom']:.1f}%, ETH dom {_fmt(glob.get('eth_dom'))}% [{glob['source']}]")
    if fng:
        L.append(f"- Crypto Fear&Greed {fng['value']} ({fng['label']}); 7d ago {fng['series'][-8]['v'] if len(fng['series']) > 8 else 'n/a'}")
    if macro:
        q = ", ".join(f"{r['label']} {_fmt(r['price'])} ({_fmt(r['chg'], pct=True)})" for r in macro["quotes"] if r["price"])
        L.append(f"- Macro: {q}")
        if macro.get("rates"):
            r = macro["rates"]
            L.append(f"- UST {r['date']}: 2Y {r['2y']}%, 10Y {r['10y']}%, 2s10s {r.get('spread_2s10s')}")
        if macro.get("cnn_fg"):
            L.append(f"- Stocks Fear&Greed (CNN) {macro['cnn_fg']['value']} ({macro['cnn_fg']['label']})")
    L += await markets.context_lines()
    if oc:
        dvb = (oc.get("dvol") or {}).get("BTC")
        if dvb:
            L.append(f"- BTC DVOL {dvb['now']:.1f} (24h ago {_fmt(dvb.get('d24'))}, 30d {dvb['lo30']:.0f}-{dvb['hi30']:.0f})")
        if oc.get("stables_chg7d") is not None:
            L.append(f"- Stablecoin supply {_fmt(oc['stables'][-1]['v'])} ({oc['stables_chg7d']:+.2f}% 7d, "
                     f"{oc['stables_chg30d']:+.2f}% 30d): liquidity proxy")
        etf = oc.get("etf") or {}
        for k in ("btc", "eth"):
            if etf.get(k):
                L.append(f"- {k.upper()} spot ETF flows US$M (last 5): " +
                         ", ".join(f"{r['date'][:6]} {r['flow']:+.0f}" for r in etf[k][-5:]))
    if tick:
        L.append("- Watchlist 24h: " + ", ".join(f"{t['sym']} {_fmt(t['last'])} ({_fmt(t['chg24'], pct=True)})" for t in tick))
    if dv:
        by = {r["sym"]: r for r in dv["rows"]}
        L.append("- Perp funding %/8h & OI: " + ", ".join(
            f"{s} {by[s]['fund8h']:+.4f} OI {_fmt(by[s]['oi'])}" for s in st["watchlist"]["crypto"][:8] if s in by))
    if liq and liq.get("total"):
        L.append(f"- Liquidations last 1h ({liq.get('source')}, listener running {liq.get('listening_min', 0)} min"
                 f"{' - window not yet full, treat as partial' if liq.get('listening_min', 0) < 60 else ''}): "
                 f"longs ${_fmt(liq['total']['long'])}, shorts ${_fmt(liq['total']['short'])}")
    if agg_news and agg_news["rows"]:
        L.append("- News/social mindshare 24h (mentions, tone -1..1, velocity): " + ", ".join(
            f"{r['sym']} {r['mentions']} ({r['sentiment']:+.2f}, {r['velocity']:.1f}x)" for r in agg_news["rows"][:10]))
    mood = await asyncio.to_thread(sentiment.market_mood)
    if mood:
        L.append("- Headline tone 0-100: " + ", ".join(f"{k} {v['score']} (n={v['n']})" for k, v in mood.items()))
    L += _elfa_lines()

    # focus assets
    for s in crypto_syms:
        sig = await _safe(signals.crypto_signal(s, with_pos=True))
        if not sig:
            continue
        L.append(f"\n### {s} (crypto)")
        L.append(_sig_lines(sig))
        L += _perf_line(await _safe(markets.row(s, "crypto")))
        cd = await _safe(crypto.coin_detail(s))
        if cd and cd.get("mcap"):
            L.append(f"- Fundamentals: mcap {_fmt(cd['mcap'])}, FDV {_fmt(cd.get('fdv'))}, circ/max "
                     f"{_fmt(cd.get('supply_issued_pct'))}%, ATH {_fmt(cd.get('ath'))}, 30d {_fmt(cd.get('chg30d'), pct=True)}, "
                     f"1y {_fmt(cd.get('chg1y'), pct=True)}, cats {', '.join(cd.get('categories', [])[:4])}")
            p = (cd.get("defi") or {}).get("protocol")
            if p:
                L.append(f"- DeFi: {p['name']} TVL {_fmt(p['tvl'])} ({_fmt(p.get('chg7d'), pct=True)} 7d), fees 30d "
                         f"{_fmt(p.get('fees30d'))}, revenue 30d {_fmt(p.get('rev30d'))}, P/E(ann.) {_fmt(p.get('pe_annualized'))}")
        preds = [c for c in predict.current() if c["sym"] == s]
        if preds:
            L.append("- Rook models P(up): " + ", ".join(
                f"{c['h']}h {c['p_up']:.2f} (edge: {c['edge']}, 10-90% cone {_fmt(c['range80'][0])}-{_fmt(c['range80'][1])}"
                + (f", cone hit-rate {c['coverage']['rate'] * 100:.0f}% of {c['coverage']['n']}" if (c.get('coverage') or {}).get('n') else "")
                + ")" for c in preds))
        xa = elfa.x_attention(s)
        if xa and xa.get("listed"):
            L.append(f"- X attention (Elfa, 24h): {xa['mentions']} mentions, {xa['chg']:+.0f}% vs prior 24h, rank #{xa['rank']}")
        tm = await _safe(elfa.top_mentions(s, fetch=False))
        if tm and tm.get("rows"):
            L.append("- Most-engaged X posts (24h): " + "; ".join(
                f"@{m['user'] or '?'} {m['views']:,} views, {m['smart_reposts']} smart reposts" for m in tm["rows"][:3]))
        heads = sentiment.news(sym=s, limit=6, hours=48)
        if heads:
            L.append("- Headlines: " + " | ".join(f"[{h['label']}] {h['title'][:110]} ({h['source']})" for h in heads))
    for s in stock_syms:
        kind = markets.kind_of(s)
        sig = await _safe(signals.stock_signal(s))
        if sig:
            L.append(f"\n### {s} ({markets.label_of(s) or s}, {kind})")
            L.append(_sig_lines(sig))
        L += _perf_line(await _safe(markets.row(s)))
        f = await _safe(stocks.fundamentals(s)) if kind == "equity" else None
        if f:
            L.append(f"- Fundamentals [{f.get('source')}]: {f.get('name') or ''} {f.get('sector') or ''}; mcap {_fmt(f.get('mcap'))}, "
                     f"P/E {_fmt(f.get('pe'))}, fwd P/E {_fmt(f.get('fpe'))}, P/S {_fmt(f.get('ps'))}, rev growth "
                     f"{_fmt((f.get('rev_growth') or 0) * 100, pct=True)}, net margin {_fmt((f.get('net_margin') or 0) * 100)}%, "
                     f"FCF {_fmt(f.get('fcf'))}, debt {_fmt(f.get('debt'))}, analysts {f.get('rec') or 'n/a'} "
                     f"target {_fmt(f.get('target_mean'))}, next earnings {f.get('next_earnings') or 'n/a'}")
        heads = sentiment.news(sym=s, limit=5, hours=72)
        if heads:
            L.append("- Headlines: " + " | ".join(f"[{h['label']}] {h['title'][:110]}" for h in heads))

    want_pf = include_portfolio if include_portfolio is not None else bool(
        re.search(r"portf|carteira|holding|allocation|aloca|wallet|my (bag|position)", user_text, re.I))
    if want_pf:
        from . import portfolio
        pf = await _safe(portfolio.analytics())
        if pf and pf.get("assets"):
            # privacy: weights and percentages only by default; never wallet addresses or labels
            full = st["ai"].get("portfolio_detail") == "full"
            L.append("\n### User's portfolio (per asset; manual holdings + tracked wallets)")
            risk = pf.get("risk") or {}
            L.append((f"- Total {_fmt(pf['total_usd'])} USD, " if full else "- ")
                     + f"PnL {_fmt(pf.get('pnl_pct'), pct=True)}, ann. vol {_fmt(risk.get('port_vol'))}%, "
                     f"max drawdown 180d {_fmt(risk.get('max_dd'))}%")
            for a in pf["assets"]:
                L.append(f"  - {a['symbol']} ({a['kind']}): {_fmt(a.get('weight'))}% weight"
                         + (f", value {_fmt(a.get('value_usd'))}" if full else "")
                         + f", vol {_fmt(a.get('vol'))}%, risk share {_fmt(a.get('risk_contrib'))}%")
            if pf.get("flags"):
                L.append("- Risk flags: " + "; ".join(pf["flags"]))
    who = user_name(st) or "The user"
    home = (st.get("profile") or {}).get("home_currency") or "USD"
    fx = (macro or {}).get("usdbrl", {}).get("rate") if macro else None
    L.append(f"\n- {who}'s risk profile: {st['risk']['profile']} (max single position {st['risk']['max_position_pct']}%, "
             f"target portfolio vol {st['risk']['target_vol_pct']}%). Timezone {now.tzname()}; amounts in USD"
             + (f" (home currency BRL, USD/BRL {_fmt(fx)})." if home == "BRL" else "."))
    L.append("- " + predict.llm_track_record()["text"])
    return "\n".join(L), syms


def _perf_line(r):
    """One line of returns (USD, BRL, vs CDI), 52w range and volatility for an asset."""
    if not r:
        return []
    ks = ("w1", "m1", "m3", "ytd", "y1")
    names = {"w1": "1W", "m1": "1M", "m3": "3M", "ytd": "YTD", "y1": "1Y"}
    if r.get("unit") == "yield":
        return ["- Yield change: " + ", ".join(f"{names[k]} {r[k]:+.0f}bp" for k in ks if r.get(k) is not None)]
    out = "- Returns: " + ", ".join(f"{names[k]} {r[k]:+.1f}%" for k in ks if r.get(k) is not None)
    if r.get("brl") and r.get("ccy") == "USD":
        out += " | in BRL: " + ", ".join(f"{names[k]} {v:+.1f}%" for k, v in r["brl"].items())
    if r.get("xcdi"):
        out += " | excess vs CDI: " + ", ".join(f"{names[k]} {v:+.1f}%" for k, v in r["xcdi"].items())
    extra = []
    if r.get("pos52") is not None:
        extra.append(f"52w range position {r['pos52'] * 100:.0f}% ({_fmt(r.get('dd52'), pct=True)} from the 52w high)")
    if r.get("vol30") is not None:
        extra.append(f"30d vol {r['vol30']:.0f}% ann.")
    if r.get("trend"):
        extra.append(f"daily trend {r['trend']} (EMA50/200)")
    return [out] + (["- " + "; ".join(extra)] if extra else [])


def _elfa_lines():
    out = []
    tr = elfa.trending()
    if tr["rows"]:
        movers = sorted(tr["rows"][:40], key=lambda r: r["chg"], reverse=True)
        out.append("- X mindshare (Elfa, 24h mentions, change vs prior 24h): top " + ", ".join(
            f"{r['sym']} {r['cur']} ({r['chg']:+.0f}%)" for r in tr["rows"][:10])
            + "; fastest risers " + ", ".join(f"{r['sym']} {r['chg']:+.0f}%" for r in movers[:5] if r["chg"] > 0))
    nar = elfa.narratives()
    if nar["rows"]:
        out.append("- Trending X narratives (Elfa): " + " | ".join(n["text"][:140] for n in nar["rows"][:5]))
    ca = elfa.cas()
    early = [r for r in ca["rows"] if r.get("early") and r.get("sym")][:4]
    if early:
        out.append("- Early on Telegram, not yet on X (Elfa lead-time play, high risk): " + ", ".join(
            f"{r['sym']} ({r['chain']}, liq {_fmt(r.get('liquidity'))})" for r in early))
    return out


def _sig_lines(sig):
    t1, td = sig.get("t1h") or {}, sig.get("t1d") or {}
    out = [f"- Rook score {sig['score']:+.0f} ({sig['label']}); components {sig['components']}"]
    ro = sig.get("readout") or {}
    if ro.get("bottom_line"):
        out.append(f"- Readout: {ro['bottom_line']}")
    lv = (sig.get("levels") or {})
    for name in ("near", "major"):
        x = lv.get(name) or {}
        if x.get("support") or x.get("resistance"):
            out.append(f"- {'4h' if name == 'near' else 'Daily'} levels: support "
                       + ", ".join(f"{_fmt(e['price'])} ({e['touches']}x)" for e in x.get("support", [])[:2])
                       + "; resistance " + ", ".join(f"{_fmt(e['price'])} ({e['touches']}x)" for e in x.get("resistance", [])[:2]))
    for a in (sig.get("setups") or [])[:4]:
        stt = a.get("stats") or {}
        out.append(f"- Setup: {a['label']} ({a['tf']}, {a['dir']})"
                   + (f": historically {stt['hit'] * 100:.0f}% vs base {stt['base'] * 100:.0f}% over {stt['horizon']}, "
                      f"n={stt['n']} -> {stt['verdict']}" if stt.get("n") else ": no history yet"))
    px = sig.get("positioning") or {}
    if px.get("funding_z") is not None:
        out.append(f"- Funding z-score vs 30d: {px['funding_z']:+.1f}" + (f"; OI/price: {px['oi_div']}" if px.get("oi_div") else ""))
    if t1:
        out.append(f"- 1h: price {_fmt(t1['price'])}, EMA9/21/50/200 {_fmt(t1.get('ema9'))}/{_fmt(t1.get('ema21'))}/"
                   f"{_fmt(t1.get('ema50'))}/{_fmt(t1.get('ema200'))} ({t1.get('ema_stack')}), RSI {_fmt(t1.get('rsi'), 0)}, "
                   f"MACD hist {_fmt(t1.get('macd_hist'))}, ATR {_fmt(t1.get('atr_pct'))}%, 50-bar range "
                   f"{_fmt(t1.get('swing_lo'))}-{_fmt(t1.get('swing_hi'))}")
    if td:
        out.append(f"- 1d: EMA21/50/200 {_fmt(td.get('ema21'))}/{_fmt(td.get('ema50'))}/{_fmt(td.get('ema200'))} "
                   f"({td.get('ema_stack')}), RSI {_fmt(td.get('rsi'), 0)}, 50-day range {_fmt(td.get('swing_lo'))}-"
                   f"{_fmt(td.get('swing_hi'))}, BB {_fmt(td.get('bb_lo'))}-{_fmt(td.get('bb_hi'))}")
    d = sig.get("deriv")
    if d:
        out.append(f"- Perps: funding {d['fund8h']:+.4f}%/8h, OI {_fmt(d.get('oi'))}"
                   + (f", OI 24h {(sig.get('positioning') or {}).get('oi_chg24') or 0:+.1f}%" if sig.get("positioning") else ""))
    s = sig.get("sentiment")
    if s:
        out.append(f"- Social/news: {s['mentions']} mentions 24h, tone {s['sentiment']:+.2f}, mindshare {s['mindshare']:.1f}%, "
                   f"velocity {s['velocity']:.1f}x")
    if sig.get("notes"):
        out.append("- Signals: " + "; ".join(sig["notes"]))
    return "\n".join(out)


# ------------------------------------------------------------------ transport
def _hermes():
    url = secret("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/")
    return url, secret("HERMES_API_KEY")


def _attempts(mode):
    ai = load_settings()["ai"]
    prim = dict(ai["primary"])
    fb = dict(ai["fallback"])
    if mode == "expert":
        prim["reasoning_effort"] = ai.get("expert_effort") or "max"
    elif mode == "utility":  # mechanical transforms (alert JSON): reasoning only adds latency
        prim["reasoning_effort"] = fb["reasoning_effort"] = "low"
        if prim.get("provider") == "elfa":  # Elfa can't follow a JSON schema; go straight to the fallback
            return [("fallback", fb)], ai
    out = [("primary", prim)]
    if (fb.get("model"), fb.get("provider")) != (prim.get("model"), prim.get("provider")) or \
            fb.get("reasoning_effort") != prim.get("reasoning_effort"):
        out.append(("fallback", fb))
    return out, ai


def _label(spec):
    if spec.get("provider") == "elfa":
        return spec.get("model") or "elfa-fast"
    return f"{spec.get('model') or 'Hermes default'}" + (f" @ {spec['reasoning_effort']}" if spec.get("reasoning_effort") else "")


async def _stream_spec(spec, messages, ai, thread=None):
    """Route one attempt to its transport: Elfa's own chat API, or the Hermes API server."""
    if spec.get("provider") == "elfa":
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        async for ev in elfa.chat_stream(user, spec.get("model") or "elfa-fast", thread):
            yield ev
        return
    async for ev in _stream_hermes(spec, messages, ai):
        yield ev


async def _stream_hermes(spec, messages, ai):
    url, key = _hermes()
    body = {"model": spec.get("model") or "hermes-agent", "messages": messages, "stream": True}
    if spec.get("provider"):
        body["provider"] = spec["provider"]
    if spec.get("reasoning_effort"):
        body["model_options"] = {"reasoning_effort": spec["reasoning_effort"]}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.time()
    got_any = False
    timeout = httpx.Timeout(connect=5, read=45, write=10, pool=5)
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream("POST", f"{url}/v1/chat/completions", json=body, headers=headers) as r:
            if r.status_code >= 400:
                txt = (await r.aread()).decode(errors="ignore")[:300]
                raise RuntimeError(f"HTTP {r.status_code}: {txt}")
            event = None
            async for line in r.aiter_lines():
                el = time.time() - t0
                if not got_any and el > ai["first_token_timeout_s"]:
                    raise TimeoutError(f"no output in {ai['first_token_timeout_s']}s")
                if el > ai["timeout_s"]:
                    raise TimeoutError(f"exceeded {ai['timeout_s']}s")
                if not line or line.startswith(":"):
                    if not line:
                        event = None
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    j = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event == "hermes.status":
                    txt = str(j.get("text") or j.get("message") or "")
                    if "fallback" in txt.lower():
                        # Hermes' own provider fallback would answer at the primary's effort; the configured
                        # fallback (e.g. gemini-3.8-flash @ max) should run instead, so surface it as a primary failure.
                        raise RuntimeError(f"Hermes: {txt.strip()[:200]}")
                    if txt:
                        yield {"type": "status", "text": txt[:160]}
                    continue
                if event and "tool" in event:
                    got_any = True
                    name = j.get("tool") or j.get("name") or j.get("tool_name") or "tool"
                    label = j.get("label") or j.get("preview") or j.get("message") or j.get("emoji") or ""
                    yield {"type": "tool", "name": name, "label": str(label)[:140]}
                    continue
                if "error" in j:
                    raise RuntimeError(str(j["error"])[:300])
                for ch in j.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("reasoning_content"):
                        got_any = True
                        yield {"type": "reasoning", "text": d["reasoning_content"]}
                    if d.get("content"):
                        got_any = True
                        yield {"type": "delta", "text": d["content"]}
                    if ch.get("finish_reason") in ("length", "content_filter"):
                        raise RuntimeError(f"finish_reason={ch['finish_reason']}")


async def _stream_cli(messages, ai):
    exe = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    fb = ai["fallback"]
    prompt = "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in messages)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(prompt + "\n\n[Respond to the last USER message.]")
        path = f.name
    args = [exe, "chat", "-Q", "--query-file", path, "-t", "web", "--max-turns", "8"]
    if fb.get("model"):
        args += ["-m", fb["model"]]
    if fb.get("provider"):
        args += ["--provider", fb["provider"]]
    if fb.get("reasoning_effort"):
        args += ["--reasoning", fb["reasoning_effort"]]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL)
    try:
        assert proc.stdout is not None
        got = False
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(256), timeout=ai["timeout_s"] + 60)
            if not chunk:
                break
            got = True
            yield {"type": "delta", "text": chunk.decode(errors="ignore")}
        await proc.wait()
        if not got:
            raise RuntimeError(f"hermes CLI produced no output (exit {proc.returncode})")
    finally:
        if proc.returncode is None:
            proc.kill()
        os.unlink(path)


_skip_until: dict[str, float] = {}  # circuit breaker: model label -> retry time
BREAKER_S = 300


async def stream(messages, mode="fast", thread=None):
    """Yields UI events. Handles fallback, mid-answer continuation and a circuit breaker on dead primaries."""
    attempts, ai = _attempts(mode)
    text = ""
    errors = []
    now = time.time()
    live_attempts = [(n, s) for n, s in attempts if _skip_until.get(_label(s), 0) <= now] or attempts[-1:]
    for n, s in attempts:
        if (n, s) not in live_attempts:
            yield {"type": "notice", "text": f"{_label(s)} skipped: it failed {int(BREAKER_S - (_skip_until[_label(s)] - now))}s "
                                             f"ago (retrying in {int(_skip_until[_label(s)] - now)}s)."}
    for name, spec in live_attempts:
        msgs = messages
        if text:
            msgs = messages + [{"role": "assistant", "content": text},
                               {"role": "user", "content": "Your previous message was cut off mid-answer. Continue "
                                                           "exactly where it stopped. Do not repeat anything."}]
        yield {"type": "meta", "attempt": name, "model": _label(spec), "provider": spec.get("provider") or "default"}
        try:
            got = ""
            async for ev in _stream_spec(spec, msgs, ai, thread):
                if ev["type"] == "delta":
                    got += ev["text"]
                yield ev
            if not got.strip():
                raise RuntimeError("empty response")
            text += got
            _skip_until.pop(_label(spec), None)
            yield {"type": "done", "model": _label(spec), "attempt": name, "content": text}
            return
        except Exception as e:  # noqa: BLE001
            text += got  # keep what was already shown so the next model continues from the cut, not from scratch
            errors.append(f"{_label(spec)}: {e}")
            if name != live_attempts[-1][0]:
                _skip_until[_label(spec)] = time.time() + BREAKER_S
            yield {"type": "notice", "text": f"{_label(spec)} failed ({str(e)[:160]}). "
                                             + ("Falling back." if name != live_attempts[-1][0] or ai.get("cli_fallback") else "")}
    if ai.get("cli_fallback"):
        yield {"type": "meta", "attempt": "cli", "model": f"hermes CLI · {_label(ai['fallback'])}", "provider": "cli"}
        try:
            msgs = messages if not text else messages + [{"role": "assistant", "content": text}, {
                "role": "user", "content": "Continue exactly where you stopped."}]
            got = ""
            async for ev in _stream_cli(msgs, ai):
                got += ev.get("text", "")
                yield ev
            text += got
            yield {"type": "done", "model": "hermes CLI", "attempt": "cli", "content": text}
            return
        except Exception as e:  # noqa: BLE001
            errors.append(f"cli: {e}")
    yield {"type": "error", "text": "All AI routes failed: " + " | ".join(errors)}


async def complete(messages, mode="fast"):
    out, model = "", None
    async for ev in stream(messages, mode):
        if ev["type"] == "done":
            return ev["content"], ev["model"]
        if ev["type"] == "error":
            raise RuntimeError(ev["text"])
    return out, model


# ------------------------------------------------------------------ chats
FORECAST_RE = re.compile(r"```forecast\s*(.*?)```", re.S)


def extract_forecasts(text):
    out = []
    for m in FORECAST_RE.finditer(text):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        for f in data if isinstance(data, list) else [data]:
            try:
                sym = str(f["symbol"]).upper().replace("USDT", "")
                hz = int(f.get("horizon_h", 24))
                p = float(f["p_up"])
                if hz in (4, 24, 168) and 0.01 <= p <= 0.99:
                    out.append((sym, hz, p, str(f.get("note", ""))))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def new_chat(title):
    cid = uuid.uuid4().hex[:12]
    db.x("INSERT INTO chats(id, title, created, updated) VALUES (?,?,?,?)", (cid, title[:80], time.time(), time.time()))
    return cid


def chat_messages(cid):
    return db.q("SELECT role, content, meta, ts FROM chat_messages WHERE chat_id=? ORDER BY id", (cid,))


async def chat_stream(cid, user_text, mode="fast", focus=None):
    if not cid or not db.q("SELECT 1 FROM chats WHERE id=?", (cid,)):
        cid = new_chat(user_text)
    yield {"type": "chat", "id": cid}
    db.x("INSERT INTO chat_messages(chat_id, role, content, meta, ts) VALUES (?,?,?,?,?)",
         (cid, "user", user_text, json.dumps({"mode": mode, "focus": focus}), time.time()))
    yield {"type": "status", "text": "Gathering live market data…"}
    ctx, syms = await build_context(user_text, focus)
    yield {"type": "context", "symbols": syms, "chars": len(ctx)}
    hist = chat_messages(cid)[:-1][-12:]
    messages = [{"role": "system", "content": system_prompt() + "\n\n" + (EXPERT_HINT if mode == "expert" else FAST_HINT)
                 + "\n\n" + ctx}]
    messages += [{"role": m["role"], "content": m["content"]} for m in hist]
    messages.append({"role": "user", "content": user_text})
    final, model, tools = None, None, []
    async for ev in stream(messages, mode, cid):
        if ev["type"] == "tool":
            tools.append(ev)
        if ev["type"] == "done":
            final, model = ev["content"], ev["model"]
        yield ev
    if final:
        logged = []
        for sym, hz, p, note in extract_forecasts(final):
            predict.log_llm_forecast(sym, hz, p, note)
            logged.append({"sym": sym, "h": hz, "p": p})
        if logged:
            yield {"type": "forecasts", "items": logged}
        db.x("INSERT INTO chat_messages(chat_id, role, content, meta, ts) VALUES (?,?,?,?,?)",
             (cid, "assistant", final, json.dumps({"model": model, "tools": tools[:20], "forecasts": logged}), time.time()))
        db.x("UPDATE chats SET updated=? WHERE id=?", (time.time(), cid))


# ------------------------------------------------------------------ brief + helpers
BRIEF_PROMPT = """Write the Rook market brief for right now.
Structure: **Regime** (one line: risk-on / neutral / risk-off and why), **Crypto** (BTC, ETH, SOL + anything notable in the
watchlist or mindshare movers), **Derivatives & flows** (funding, OI, liquidations, ETF flows, stablecoin liquidity),
**Macro** (equities, DXY, yields, VIX, USD/BRL), **What would change my mind**, **Watch next 24h** (3 bullets max).
Keep it under 350 words and include a forecast block for BTC 24h."""


async def brief():
    ctx, _ = await build_context("BTC ETH SOL market brief", ["BTC", "ETH", "SOL"])
    msgs = [{"role": "system", "content": system_prompt() + "\n\n" + FAST_HINT + "\n\n" + ctx},
            {"role": "user", "content": BRIEF_PROMPT}]
    text, model = await complete(msgs, "fast")
    for sym, hz, p, note in extract_forecasts(text):
        predict.log_llm_forecast(sym, hz, p, note)
    db.x("INSERT INTO briefs(ts, content, model) VALUES (?,?,?)", (time.time(), text, model))
    return {"ts": time.time(), "content": text, "model": model}


ALERT_SCHEMA = """Return ONLY JSON (no prose) matching:
{"name": str, "logic": "all"|"any", "cooldown_min": int,
 "conditions": [{"sym": str, "kind": "crypto"|"stock"|"market", "metric": METRIC, "op": ">"|"<"|"crosses_above"|"crosses_below", "value": number}]}
METRIC one of: price, chg24, rsi_1h, rsi_1d, dist_ema200_1d (percent above/below daily EMA200), ema9_21_1h (value 0, use crosses_above/crosses_below),
funding (percent per 8h), oi_chg24 (percent), sentiment (-1..1), mentions (24h count), velocity (x), score (Rook score -100..100),
p_up_24 (model probability 0..1), fng (crypto fear&greed 0-100, sym "MARKET"), liq_1h (USD liquidated last hour, sym "MARKET").
Use kind "market" for fng/liq_1h."""


async def parse_alert(text):
    msgs = [{"role": "system", "content": ALERT_SCHEMA},
            {"role": "user", "content": f"Turn this into an alert spec: {text}"}]
    out, _ = await complete(msgs, "utility")
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise ValueError("model did not return JSON")
    return json.loads(m.group(0))


async def model_options():
    url, key = _hermes()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{url}/api/model/options", headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        d = r.json()
    provs = []
    for p in d.get("providers", []):
        if not p.get("authenticated"):
            continue
        ms = [m.get("id") if isinstance(m, dict) else m for m in p.get("models") or []]
        provs.append({"provider": p.get("slug") or p.get("id") or p.get("provider"), "name": p.get("name"),
                      "models": [m for m in ms if m]})
    ok, why = elfa.chat_available()
    provs.append({"provider": "elfa", "name": "Elfa (X/Telegram-native AI, own data)", "models": list(elfa.CHAT_MODELS),
                  "available": ok, "note": why or "Answers from Elfa's social data; Rook's live-data context is not sent."})
    return {"default": {"model": d.get("model"), "provider": d.get("provider")}, "providers": provs}


async def health():
    url, key = _hermes()
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{url}/health")
            return {"ok": r.status_code == 200, "url": url, "key_set": bool(key)}
    except httpx.HTTPError as e:
        return {"ok": False, "url": url, "key_set": bool(key), "error": str(e)}
