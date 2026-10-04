"""News + social ingestion, lexicon sentiment, ticker detection, mindshare / mention velocity (Elfa-style)."""
import asyncio
import hashlib
import math
import re
import time
from calendar import timegm

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .. import db, net
from ..config import secret
from ..net import UA_BROWSER, cached, chain, get_json, get_text

FEEDS = [
    # (source, kind, url, weight)
    ("CoinDesk", "crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/", 1.3),
    ("The Block", "crypto", "https://www.theblock.co/rss.xml", 1.3),
    ("Decrypt", "crypto", "https://decrypt.co/feed", 1.1),
    ("Blockworks", "crypto", "https://blockworks.co/feed", 1.2),
    ("Bitcoin Magazine", "crypto", "https://bitcoinmagazine.com/.rss/full/", 1.0),
    ("CryptoSlate", "crypto", "https://cryptoslate.com/feed/", 1.0),
    ("NewsBTC", "crypto", "https://www.newsbtc.com/feed/", 0.7),
    ("Google News", "crypto", "https://news.google.com/rss/search?q=crypto+OR+bitcoin+OR+ethereum+when:1d&hl=en-US&gl=US&ceid=US:en", 0.9),
    ("CNBC Markets", "macro", "https://www.cnbc.com/id/100003114/device/rss/rss.html", 1.2),
    ("MarketWatch", "macro", "https://feeds.marketwatch.com/marketwatch/topstories/", 1.1),
    ("Google News", "macro", "https://news.google.com/rss/search?q=stock+market+OR+fed+OR+inflation+when:1d&hl=en-US&gl=US&ceid=US:en", 0.9),
    ("InfoMoney", "brazil", "https://www.infomoney.com.br/feed/", 0.8),
]
SUBREDDITS = [("CryptoCurrency+Bitcoin+ethereum+solana+CryptoMarkets", "crypto"),
              ("wallstreetbets+stocks+investing", "stocks")]
JUNK = re.compile(r"price on .{3,30} prediction market|price prediction \d{4}|sponsored|press release", re.I)

_VADER = SentimentIntensityAnalyzer()
_VADER.lexicon.update({
    "bullish": 2.6, "bearish": -2.6, "moon": 2.0, "mooning": 2.5, "pump": 1.0, "pumps": 1.2, "pumping": 1.4,
    "dump": -2.0, "dumps": -2.0, "dumping": -2.2, "rug": -3.0, "rugpull": -3.2, "rugged": -3.0, "hack": -2.8,
    "hacked": -3.0, "exploit": -2.8, "exploited": -3.0, "drained": -2.8, "liquidated": -1.8, "liquidations": -1.4,
    "ath": 2.2, "rally": 2.0, "rallies": 2.0, "rallied": 2.0, "surge": 2.1, "surges": 2.1, "surged": 2.1,
    "soar": 2.2, "soars": 2.2, "soared": 2.2, "jumps": 1.6, "climbs": 1.4, "gains": 1.4, "plunge": -2.6,
    "plunges": -2.6, "plunged": -2.6, "crash": -3.0, "crashes": -3.0, "crashed": -3.0, "tumbles": -2.2,
    "slump": -2.0, "slumps": -2.0, "selloff": -2.2, "sell-off": -2.2, "outflows": -1.2, "inflows": 1.2,
    "approval": 1.6, "approves": 1.6, "approved": 1.6, "rejects": -1.6, "rejected": -1.6, "lawsuit": -1.8,
    "sues": -1.8, "fud": -1.5, "hodl": 1.0, "breakout": 1.8, "breakdown": -1.8, "capitulation": -2.0,
    "accumulation": 1.2, "accumulating": 1.2, "upgrade": 1.3, "downgrade": -1.6, "downgrades": -1.6, "beats": 1.5,
    "misses": -1.5, "bankruptcy": -3.0, "insolvent": -3.0, "delisting": -2.2, "delists": -2.0, "partnership": 1.2,
    "adoption": 1.5, "ban": -2.0, "bans": -2.0, "tariff": -1.0, "tariffs": -1.2, "recession": -2.0, "layoffs": -1.6,
    "stagflation": -2.0, "hawkish": -1.2, "dovish": 1.2, "cut": 0.3, "hike": -0.8, "record": 1.0, "slides": -1.5,
    "sinks": -1.8, "falls": -1.2, "drops": -1.3, "rebounds": 1.6, "rebound": 1.4, "recovers": 1.4, "warning": -1.2,
    "alta": 1.2, "queda": -1.2, "dispara": 1.8, "despenca": -2.2, "sobe": 1.0, "cai": -1.0,
})

# Tickers that are also English words: only count them with a $cashtag or their full name.
AMBIGUOUS = {"ONE", "FUN", "GAS", "SUN", "TON", "NEAR", "LINK", "DOT", "APE", "OP", "ARB", "SAND", "MANA", "FLOW",
             "HOT", "CAKE", "MOVE", "ME", "AI", "S", "IT", "USD", "USDT", "USDC", "WLD", "BEAM", "JUP", "W", "TRUMP",
             "PEOPLE", "HIGH", "LOW", "BIG", "MAGIC", "ROSE", "GLM", "BLUR", "PENDLE", "ENA", "PYTH", "SEI", "TIA",
             "STRK", "IMX", "RUNE", "KAS", "ICP", "CRO", "LEO", "OM", "GT", "A", "U", "FET", "XLM", "UNI", "AAVE",
             "SPY", "QQQ", "COIN", "META", "NOW", "ALL", "ARE", "CAT", "DE", "GS", "MA", "ON", "SO", "T", "V"}
STABLES = {"USDT", "USDC", "DAI", "FDUSD", "USDE", "TUSD", "PYUSD", "USDS", "USD1", "BUSD", "USDD"}
# Coin *names* that are ordinary English words: never match by name (needs $TICKER or curated override).
NAME_STOP = {"just", "near", "sun", "gas", "one", "flow", "magic", "sand", "beam", "core", "story", "sonic", "maker",
             "stacks", "render", "sky", "usual", "curve", "convex", "compound", "blur", "mantle", "plasma", "movement",
             "dash", "gala", "cosmos", "stellar", "ripple", "optimism", "immutable", "polygon", "injective", "jupiter",
             "gate", "aster", "bonk", "official trump", "kaia", "mask", "zora", "space", "safe", "tether", "jasmy",
             "pump", "walrus", "sonic", "celo", "venom", "frax", "lido", "chiliz", "virtual", "virtuals", "ether"}

NAME_OVERRIDES = {"BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "SOL": ["solana"], "XRP": ["ripple", "xrp"],
                  "BNB": ["bnb", "binance coin"], "DOGE": ["dogecoin"], "ADA": ["cardano"], "HYPE": ["hyperliquid"],
                  "TON": ["toncoin"], "LINK": ["chainlink"], "DOT": ["polkadot"], "AVAX": ["avalanche"],
                  "SUI": ["sui network"], "NEAR": ["near protocol"], "MSTR": ["microstrategy"],
                  "COIN": ["coinbase"], "NVDA": ["nvidia"], "TSLA": ["tesla"], "AAPL": ["apple inc", "iphone maker"],
                  "MSFT": ["microsoft"], "PETR4.SA": ["petrobras"], "VALE3.SA": ["vale s.a", "mineradora vale"]}

_matchers: list[tuple[str, re.Pattern]] = []
_matchers_ts = 0.0


async def _build_matchers(extra_syms=()):
    global _matchers, _matchers_ts
    if _matchers and time.time() - _matchers_ts < 3600:
        return
    from . import crypto
    syms = {}
    bare_ok = {s.upper() for s in extra_syms}
    try:
        m = await crypto.markets(150)
        for r in m["rows"]:
            if r["sym"] in STABLES:
                continue
            syms[r["sym"]] = [r["name"].lower()]
            if (r.get("rank") or 999) <= 50:
                bare_ok.add(r["sym"])
    except Exception:  # noqa: BLE001
        pass
    for s in extra_syms:
        syms.setdefault(s.upper(), [])
    for s, names in NAME_OVERRIDES.items():
        syms[s] = list(dict.fromkeys(names + syms.get(s, [])))
    # Matching rules (precision over recall):
    #  - $CASHTAG always counts.
    #  - bare UPPERCASE ticker counts only for top-50 coins + the user's watchlist (and not ambiguous words).
    #  - curated names (NAME_OVERRIDES: bitcoin, ethereum...) match case-insensitively.
    #  - other coin names must appear Capitalised and must not be ordinary English words (NAME_STOP).
    matchers = []
    for s, names in syms.items():
        base = s.split(".")[0]
        parts_cs = [rf"\${re.escape(base)}\b"]
        if s in bare_ok and base not in AMBIGUOUS and len(base) >= 3 and base.isalpha():
            parts_cs.append(rf"\b{re.escape(base)}\b")
        curated = set(NAME_OVERRIDES.get(s, []))
        for n in names:
            if n in curated or len(n) < 4 or n.lower() in NAME_STOP or n.lower() in ("token", "coin", "network", "protocol", "the", "apple"):
                continue
            parts_cs.append(rf"\b{re.escape(n[:1].upper() + n[1:])}\b")
        parts_ci = [rf"\b{re.escape(n)}\b" for n in curated if len(n) >= 4]
        matchers.append((s, re.compile("|".join(parts_cs))))
        if parts_ci:
            matchers.append((s, re.compile("|".join(parts_ci), re.I)))
    _matchers = matchers
    _matchers_ts = time.time()


def detect(text):
    found = []
    for s, p in _matchers:
        if s not in found and p.search(text):
            found.append(s)
    return found


async def retag(extra_syms=()):
    """Re-run ticker detection over stored items (after matcher rule changes)."""
    global _matchers_ts
    _matchers_ts = 0
    await _build_matchers(extra_syms)
    rows = db.q("SELECT id, title, source FROM news WHERE source != 'CryptoPanic'")
    db.xmany("UPDATE news SET symbols=? WHERE id=?", [(",".join(detect(r["title"])), r["id"]) for r in rows])
    return len(rows)


def score(text):
    return _VADER.polarity_scores(text)["compound"]


def _ts(entry):
    for k in ("published_parsed", "updated_parsed"):
        v = entry.get(k)
        if v:
            return float(timegm(v))
    return time.time()


async def _feed(source, kind, url, weight):
    txt = await get_text(url, ua=UA_BROWSER, headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*"},
                         timeout=15)
    f = feedparser.parse(txt)
    items = []
    for e in f.entries[:40]:
        title = re.sub(r"\s+", " ", (e.get("title") or "")).strip()
        if not title:
            continue
        summ = re.sub(r"<[^>]+>", " ", e.get("summary") or "")[:300]
        src = source
        if source == "Google News" and " - " in title:
            title, src = title.rsplit(" - ", 1)
            src = f"{src} (GN)"
        items.append({"source": src, "kind": kind, "title": title, "url": e.get("link"), "published": _ts(e),
                      "text": f"{title}. {summ}", "weight": weight, "score": 0})
    return items


async def _reddit(sub, kind):
    async def rss():
        txt = await get_text(f"https://www.reddit.com/r/{sub}/hot/.rss", params={"limit": 75}, ua=UA_BROWSER,
                             timeout=15)
        f = feedparser.parse(txt)
        return [{"source": "r/" + (e.get("tags") or [{}])[0].get("term", sub.split("+")[0]), "kind": kind,
                 "title": e.get("title", ""), "url": e.get("link"),
                 "published": _ts(e), "text": e.get("title", ""), "weight": 0.6, "score": 0}
                for e in f.entries[:75] if e.get("title")]

    async def js():
        d = await get_json(f"https://old.reddit.com/r/{sub}/hot.json", params={"limit": 75},
                           ua="rookery/0.1 (local market dashboard)")
        out = []
        for c in d["data"]["children"]:
            p = c["data"]
            if p.get("stickied"):
                continue
            out.append({"source": f"r/{p.get('subreddit', sub)}", "kind": kind, "title": p["title"],
                        "url": "https://reddit.com" + p["permalink"], "published": float(p["created_utc"]),
                        "text": f"{p['title']}. {(p.get('selftext') or '')[:300]}",
                        "weight": 0.6 + min(math.log1p(p.get("score", 0)) / 10, 0.6), "score": p.get("score", 0)})
        return out

    items, _ = await chain("reddit", [("reddit-rss", rss), ("reddit-json", js)], cooldown_s=120)
    return items


async def _cryptopanic():
    key = secret("CRYPTOPANIC_API_KEY")
    if not key:
        return []
    d = await get_json("https://cryptopanic.com/api/developer/v2/posts/", params={"auth_token": key, "public": "true"})
    out = []
    for p in d.get("results", [])[:50]:
        v = p.get("votes") or {}
        bias = (v.get("positive", 0) + v.get("liked", 0) - v.get("negative", 0) - v.get("disliked", 0))
        out.append({"source": "CryptoPanic", "kind": "crypto", "title": p.get("title", ""), "url": p.get("url") or "",
                    "published": time.time(), "text": p.get("title", ""), "weight": 1.0, "score": bias,
                    "symbols": [c["code"] for c in (p.get("currencies") or [])]})
    return out


async def _reddit_all():
    async def fetch():
        out = []
        for i, (sub, kind) in enumerate(SUBREDDITS):
            if i:
                await asyncio.sleep(2.5)  # reddit 429s bursts
            out += await _reddit(sub, kind)
        return out
    # reddit rate-limits aggressively: at most one sweep per 10 min; failures back off via chain cooldown
    return await cached("reddit:sweep", 600, fetch)


async def ingest(extra_syms=()):
    """Pull every feed concurrently, score + tag, upsert. Returns number of new items."""
    await _build_matchers(extra_syms)
    jobs = [(f"{s} ({k})", _feed(s, k, u, w)) for s, k, u, w in FEEDS]
    jobs.append(("reddit", _reddit_all()))
    jobs.append(("CryptoPanic", _cryptopanic()))
    res = await asyncio.gather(*(j for _, j in jobs), return_exceptions=True)
    rows = []
    now = time.time()
    for (name, _), r in zip(jobs, res):
        src = name.split(" (")[0]
        if isinstance(r, BaseException):
            net.HEALTH.fail("news", src, r)
            continue
        if not r:
            continue
        net.HEALTH.ok("news", src, 0)
        for it in r:
            if now - it["published"] > 3 * 86400 or JUNK.search(it["title"]):
                continue
            syms = it.get("symbols") or detect(it["text"])
            s = score(it["text"])
            uid = hashlib.sha1((it["url"] or it["title"]).encode()).hexdigest()[:20]
            rows.append((uid, it["source"], it["kind"], it["title"][:300], it["url"], min(it["published"], now),
                         ",".join(syms), s, int(it.get("score") or 0), now))
    before = db.q("SELECT COUNT(*) n FROM news")[0]["n"]
    db.xmany("INSERT OR IGNORE INTO news(id, source, kind, title, url, published, symbols, sentiment, score, fetched)"
             " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    db.x("DELETE FROM news WHERE published < ?", (now - 14 * 86400,))
    after = db.q("SELECT COUNT(*) n FROM news")[0]["n"]
    return max(after - before, 0)


def news(sym=None, kind=None, limit=60, hours=72):
    since = time.time() - hours * 3600
    sql = "SELECT * FROM news WHERE published > ?"
    args: list = [since]
    if sym:
        sql += " AND (',' || symbols || ',') LIKE ?"
        args.append(f"%,{sym.upper()},%")
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY published DESC LIMIT ?"
    args.append(limit)
    rows = db.q(sql, args)
    for r in rows:
        r["symbols"] = [s for s in (r["symbols"] or "").split(",") if s]
        r["label"] = "bullish" if r["sentiment"] >= 0.25 else "bearish" if r["sentiment"] <= -0.25 else "neutral"
    return rows


def aggregate(window_h=24, recent_h=4):
    """Per-symbol mentions, weighted sentiment, mindshare %, velocity (recent rate / window rate)."""
    now = time.time()
    rows = db.q("SELECT symbols, sentiment, published, source, score FROM news WHERE published > ? AND symbols != ''",
                (now - window_h * 3600,))
    agg = {}
    total = 0
    for r in rows:
        w = 1.0 + min(math.log1p(max(r["score"] or 0, 0)) / 5, 1.0)
        age_h = (now - r["published"]) / 3600
        decay = 0.5 ** (age_h / 12)  # 12h half-life on sentiment weight
        for s in r["symbols"].split(","):
            a = agg.setdefault(s, {"sym": s, "mentions": 0, "recent": 0, "sw": 0.0, "w": 0.0, "pos": 0, "neg": 0})
            a["mentions"] += 1
            total += 1
            if age_h <= recent_h:
                a["recent"] += 1
            a["sw"] += r["sentiment"] * w * decay
            a["w"] += w * decay
            a["pos"] += r["sentiment"] >= 0.25
            a["neg"] += r["sentiment"] <= -0.25
    out = []
    for a in agg.values():
        rate_recent = a["recent"] / recent_h
        rate_window = a["mentions"] / window_h
        out.append({"sym": a["sym"], "mentions": a["mentions"], "sentiment": a["sw"] / a["w"] if a["w"] else 0.0,
                    "mindshare": a["mentions"] / total * 100 if total else 0.0,
                    "velocity": rate_recent / rate_window if rate_window else 0.0,
                    "bull": a["pos"], "bear": a["neg"]})
    out.sort(key=lambda a: a["mentions"], reverse=True)
    return {"window_h": window_h, "total_mentions": total, "rows": out}


def market_mood(window_h=24):
    """Overall crowd tone 0..100 from all items (crypto + macro separately)."""
    now = time.time()
    out = {}
    for kind in ("crypto", "macro", "stocks"):
        rows = db.q("SELECT sentiment FROM news WHERE published > ? AND kind = ?", (now - window_h * 3600, kind))
        if rows:
            m = sum(r["sentiment"] for r in rows) / len(rows)
            out[kind] = {"score": round(50 + 50 * max(-1, min(1, m * 2.5))), "n": len(rows), "mean": m}
    return out


def record_hist():
    agg = aggregate()
    ts = time.time() // 3600 * 3600
    db.xmany("INSERT OR REPLACE INTO sentiment_hist(ts, symbol, mentions, sentiment, mindshare, velocity)"
             " VALUES (?,?,?,?,?,?)", [(ts, a["sym"], a["mentions"], a["sentiment"], a["mindshare"], a["velocity"])
                                       for a in agg["rows"][:80]])


def hist(sym, days=14):
    return db.q("SELECT ts, mentions, sentiment, mindshare, velocity FROM sentiment_hist WHERE symbol = ? AND ts > ?"
                " ORDER BY ts", (sym.upper(), time.time() - days * 86400))


async def crypto_fear_greed():
    async def alt():
        d = await get_json("https://api.alternative.me/fng/", params={"limit": 90})
        rows = d["data"]
        return {"value": int(rows[0]["value"]), "label": rows[0]["value_classification"],
                "series": [{"t": int(r["timestamp"]), "v": int(r["value"])} for r in reversed(rows)]}

    async def cmc():
        d = await get_json("https://api.coinmarketcap.com/data-api/v3/fear-greed/chart",
                           params={"start": int(time.time()) - 90 * 86400, "end": int(time.time())})
        rows = d["data"]["dataList"]
        last = rows[-1]
        return {"value": int(last["score"]), "label": last.get("name") or "",
                "series": [{"t": int(r["timestamp"]), "v": int(r["score"])} for r in rows]}

    async def fetch():
        v, src = await chain("crypto-fear-greed", [("alternative.me", alt), ("coinmarketcap", cmc)], cooldown_s=120)
        return {**v, "source": src}
    return await cached("fng", 600, fetch)


async def elfa_trending():
    """Optional: real X/Telegram mindshare from Elfa if the user adds a key."""
    key = secret("ELFA_API_KEY")
    if not key:
        return None

    async def fetch():
        d = await get_json("https://api.elfa.ai/v2/aggregations/trending-tokens",
                           headers={"x-elfa-api-key": key}, params={"timeWindow": "24h", "pageSize": 30})
        net.HEALTH.ok("social", "elfa", 0)
        return d.get("data") or d
    try:
        return await cached("elfa:trending", 600, fetch)
    except Exception as e:  # noqa: BLE001
        net.HEALTH.fail("social", "elfa", e)
        return None
