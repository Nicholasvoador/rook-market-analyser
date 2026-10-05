"""Headless e2e for Rook Market Analyser: every page at 100%/115%/160% scale, console errors, overflow, a11y basics.
Read-only: never saves settings (scale is applied in-page), never sends AI questions, never adds data.

    brave --headless=new --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
    backend/.venv/bin/python scripts/e2e/e2e.py [screenshot-dir]

Needs the app running on 127.0.0.1:8787 (or ROOK_URL). Exit code 1 if any page fails."""
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import session, shot  # noqa: E402

BASE = os.environ.get("ROOK_URL", "http://127.0.0.1:8787").rstrip("/") + "/"
FAILED = []
ALL_ERRS: list[str] = []  # every console error of the session (per-page lists are cleared)
LIVE_STATE = """(() => {
  const L = window.__rook && window.__rook.live
  if (!L) return 'no __rook handle (page not loaded?) url=' + location.href
  const s = (x) => x ? { closed: x.closed, ready: x.ws ? x.ws.readyState : null, urlIdx: x.urlIdx, backoff: x.backoff, n: x.urls.length } : null
  return JSON.stringify({ bn: s(L.bn), bb: s(L.bb), ok: s(L.ok), nBn: L.bnStreams.size, venues: L.venue.size, pending: L.pending.size,
    retries: Object.fromEntries(L.retries), prices: L.prices.size, lastMsgAgo: L.lastMsg ? Math.round((Date.now() - L.lastMsg) / 1000) : null,
    uptime: Math.round(performance.now() / 1000) })
})()"""
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
PAGES = [
    ("dashboard", "#/", ["X mindshare", "Narratives on X", "Trending contracts", "Funding"]),
    ("mkt-overview", "#/markets/overview", ["Cross-asset backdrop", "Relative performance", "What moves Bitcoin", "Real rate", "CDI"]),
    ("mkt-crypto", "#/markets/crypto", ["Top by market cap", "52w range"]),
    ("mkt-stocks", "#/markets/stocks", ["US mega caps", "Brazil (B3)", "Axia"]),
    ("mkt-commod", "#/markets/commodities", ["Gold", "Agriculture"]),
    ("mkt-indices", "#/markets/indices", ["S&P 500", "Ibovespa"]),
    ("mkt-fx", "#/markets/fx", ["USD/BRL"]),
    ("mkt-rates", "#/markets/rates", ["Real rate", "Treasury curve", "10-year"]),
    ("mkt-etfs", "#/markets/etfs", ["US sectors"]),
    ("asset-gold", "#/asset/stock/GC%3DF", ["Gold", "Performance", "Bottom line"]),
    ("signals", "#/signals", ["Scanner", "Setups firing now", "Live track record"]),
    ("asset-btc", "#/asset/crypto/BTC", ["Bottom line", "Levels", "Setups"]),
    ("asset-nvda", "#/asset/stock/NVDA", ["Bottom line"]),
    ("asset-dex", f"#/asset/dex/{BONK}", ["Bonk", "Liquidity"]),
    ("predictions", "#/predictions", ["Cone hit-rate"]),
    ("portfolio", "#/portfolio", ["Solana wallets", "weights"]),
    ("alerts", "#/alerts", ["Alerts"]),
    ("ask", "#/ask", ["Ask Rook"]),
    ("settings", "#/settings", ["Interface size", "Credits this month", "Live feeds", "Solana RPC"]),
]

A11Y = r"""(() => {
  const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
  const name = (e) => (e.getAttribute('aria-label') || e.getAttribute('title') || e.innerText || e.value || '').trim()
  const btns = [...document.querySelectorAll('button, [role=button], a[href]')].filter(vis).filter((e) => !name(e) && !e.querySelector('img[alt]'))
  const inputs = [...document.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(vis).filter((e) => {
    if (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.getAttribute('title')) return false
    if (e.id && document.querySelector(`label[for="${e.id}"]`)) return false
    return !e.closest('label')
  })
  const ids = {}; document.querySelectorAll('[id]').forEach((e) => (ids[e.id] = (ids[e.id] || 0) + 1))
  const dupIds = Object.entries(ids).filter(([, n]) => n > 1).map(([k]) => k)
  const imgs = [...document.querySelectorAll('img')].filter((e) => !e.hasAttribute('alt')).length
  const main = document.querySelector('main, [role=main]')
  const h1 = document.querySelectorAll('h1').length
  const fs = getComputedStyle(document.body).fontSize
  const tiny = [...document.querySelectorAll('body *')].filter(vis).filter((e) => e.childNodes.length && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()))
    .filter((e) => parseFloat(getComputedStyle(e).fontSize) < 11.5).length
  const vw = window.innerWidth
  // reachable by scrolling = fine; truncated text or off-screen with no scroll container = clipped
  const inScroller = (e) => {
    for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX
      if ((o === 'auto' || o === 'scroll') && p.scrollWidth > p.clientWidth + 1) return true
    }
    return false
  }
  const clipped = [...document.querySelectorAll('button, a.btn, .seg *, .chip')].filter(vis).filter((e) => {
    const r = e.getBoundingClientRect()
    const cs = getComputedStyle(e)
    const truncated = (cs.overflow === 'hidden' || cs.textOverflow === 'ellipsis') && e.scrollWidth > e.clientWidth + 1
    return truncated || ((r.right > vw + 1 || r.left < -1) && !inScroller(e))
  }).map((e) => (e.innerText || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30))
  const over = document.documentElement.scrollWidth - window.innerWidth
  const mainEl = document.querySelector('.main'); const mainOver = mainEl ? mainEl.scrollWidth - mainEl.clientWidth : 0
  return { unnamed: btns.slice(0, 6).map((e) => e.outerHTML.slice(0, 120)), unnamedN: btns.length,
           unlabeled: inputs.slice(0, 6).map((e) => e.outerHTML.slice(0, 120)), unlabeledN: inputs.length,
           dupIds, imgsNoAlt: imgs, clipped: clipped.slice(0, 6), clippedN: clipped.length, hasMain: !!main, h1, bodyFont: fs, tinyText: tiny, overflowX: over, mainOverflowX: mainOver }
})()"""


TAPE = "[...document.querySelectorAll('.tape-item')].slice(0, 3).map(e => e.innerText.replace(/\\n/g, ' ')).join(' | ')"


async def live_check(c, when, timeout=30):
    """The live exchange feed must deliver prices to the ticker tape (direct browser -> exchange WebSockets)."""
    tape = ""
    for _ in range(timeout * 2):
        tape = await c.eval(TAPE) or ""
        if re.search(r"BTC [\d,]+\.\d+", tape):
            print(f"OK  live feed at {when}: {tape}")
            return
        await asyncio.sleep(0.5)
    FAILED.append(f"live:{when}")
    print(f"!!  live feed at {when}: no prices after {timeout}s: {tape!r}")
    print("      live state:", await c.eval(LIVE_STATE))
    ALL_ERRS.extend(c.errors())
    for e in dict.fromkeys(ALL_ERRS):
        print("      console:", e[:240])


async def wait_text(c, needles, timeout=25):
    for _ in range(timeout * 2):
        txt = await c.eval("document.body.innerText")
        missing = [n for n in needles if n.lower() not in (txt or "").lower()]
        if not missing:
            return []
        await asyncio.sleep(0.5)
    return missing


async def steps(c):
    report = {}
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "rook-e2e")
    os.makedirs(out, exist_ok=True)
    await c.call("Page.navigate", url=BASE)
    await asyncio.sleep(4)
    await live_check(c, "start")
    for scale in (1.0, 1.15, 1.6):
        for name, route, needles in PAGES:
            ALL_ERRS.extend(c.errors())
            c.events.clear()
            await c.eval(f"location.hash = '{route}'")
            await c.eval(f"document.documentElement.style.setProperty('--ui-scale', '{scale}')")
            missing = await wait_text(c, needles, 30 if scale == 1.0 else 12)
            await asyncio.sleep(2.5 if scale == 1.0 else 1.0)
            a = await c.eval(A11Y)
            # "Ping received after close": Chrome logs this when an exchange's keep-alive ping lands after we deliberately
            # closed a chart socket on navigation (close-handshake race). Benign and not preventable from the page.
            errs = [e for e in c.errors() if "favicon" not in e and "Ping received after close" not in e]
            key = f"{name}@{int(scale * 100)}"
            report[key] = {"missing": missing, "errors": errs[:5], **a}
            flag = "OK " if (not missing and not errs and a["overflowX"] <= 0 and not a["unnamedN"] and not a["unlabeledN"]
                             and not a["dupIds"] and not a["clippedN"]) else "!! "
            if flag != "OK ":
                FAILED.append(key)
            print(f"{flag}{key:22s} miss={missing} err={len(errs)} overX={a['overflowX']} mainX={a['mainOverflowX']} "
                  f"unnamed={a['unnamedN']} unlabeled={a['unlabeledN']} clipped={a['clippedN']} dup={a['dupIds'][:3]} h1={a['h1']} "
                  f"font={a['bodyFont']} tiny={a['tinyText']}")
            if a["clippedN"]:
                print("      clipped:", a["clipped"])
            for e in errs[:3]:
                print("     ", e[:220])
            for u in a["unnamed"][:3] + a["unlabeled"][:3]:
                print("      a11y:", u)
            if scale in (1.15, 1.6) and name in ("dashboard", "asset-btc", "signals", "mkt-overview", "mkt-commod", "mkt-rates", "asset-gold", "mkt-stocks"):
                await shot(c, f"{out}/{key}.png")
    await c.eval("location.hash = '#/'")
    await live_check(c, "end (after visiting every page)")
    # keyboard: skip link is the first tab stop and moves focus to main
    await c.eval("location.hash = '#/'")
    await asyncio.sleep(1)
    await c.call("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
    await c.call("Input.dispatchKeyEvent", type="keyUp", key="Tab", code="Tab", windowsVirtualKeyCode=9)
    first = await c.eval("document.activeElement && (document.activeElement.className + '|' + document.activeElement.textContent)")
    print("first tab stop:", first)
    # A+/A- buttons exist and are labelled
    print("scale buttons:", await c.eval("[...document.querySelectorAll('.scalebtns button')].map(b => b.getAttribute('aria-label'))"))
    json.dump(report, open(f"{out}/report.json", "w"), indent=1)
    pages_failed = [f for f in FAILED if not f.startswith("live:")]
    print(f"\n{len(PAGES) * 3 - len(pages_failed)}/{len(PAGES) * 3} page checks passed, live feed "
          f"{'FAILED' if len(FAILED) > len(pages_failed) else 'ok'} · screenshots + report.json in {out}")


asyncio.run(session(steps))
sys.exit(1 if FAILED else 0)
