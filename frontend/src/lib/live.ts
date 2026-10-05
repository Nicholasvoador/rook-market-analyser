// Direct exchange WebSockets for lowest-latency prices. Browser -> exchange, no backend hop.
// Venue choice is latency-first: the browser measures each venue's real feed latency (exchange event time ->
// arrival here) and streams every symbol from the fastest venue that lists it (25% hysteresis so it doesn't flap).
// Kline + book streams are per-chart. DEX-only tokens are polled through the backend (Jupiter, ~3s).
import { api } from './api'

export type Venue = 'binance' | 'bybit' | 'okx'
export type Tick = { sym: string; price: number; chg24?: number; ts: number; venue: Venue | 'dex' }
type Cb = (t: Tick) => void

// ------------------------------------------------------------------ venue latency probes (browser side)
export type VenueLat = { ms: number | null; connect: number | null; n: number }
const LAT_KEY = 'rook.venueLat.v1'
const PROBE_TTL = 20 * 60 * 1000
const PROBES: Record<string, { url: string; sub?: unknown; ts: (m: any) => number | null }> = {
  binance: { url: 'wss://stream.binance.com:9443/ws/btcusdt@aggTrade', ts: (m) => (m.E ? +m.E : null) },
  'binance.vision': { url: 'wss://data-stream.binance.vision/ws/btcusdt@aggTrade', ts: (m) => (m.E ? +m.E : null) },
  bybit: {
    url: 'wss://stream.bybit.com/v5/public/spot',
    sub: { op: 'subscribe', args: ['publicTrade.BTCUSDT'] },
    ts: (m) => (m.topic && m.ts ? +m.ts : null),
  },
  okx: {
    url: 'wss://ws.okx.com:8443/ws/v5/public',
    sub: { op: 'subscribe', args: [{ channel: 'trades', instId: 'BTC-USDT' }] },
    ts: (m) => (m.data?.[0]?.ts ? +m.data[0].ts : null),
  },
}

function loadLat(): { t: number; res: Record<string, VenueLat> } {
  try {
    return JSON.parse(localStorage.getItem(LAT_KEY) || '') || { t: 0, res: {} }
  } catch {
    return { t: 0, res: {} }
  }
}
export let venueLat = loadLat()
const latSubs = new Set<() => void>()
export const onVenueLat = (f: () => void) => (latSubs.add(f), () => latSubs.delete(f))

function probeOne(name: string, ms = 5000): Promise<VenueLat> {
  const p = PROBES[name]
  return new Promise((resolve) => {
    const t0 = performance.now()
    let connect: number | null = null
    const samples: number[] = []
    let ws: WebSocket
    try {
      ws = new WebSocket(p.url)
    } catch {
      resolve({ ms: null, connect: null, n: 0 })
      return
    }
    const done = () => {
      try {
        ws.close()
      } catch {
        /* closed */
      }
      samples.sort((a, b) => a - b)
      resolve({ ms: samples.length ? samples[Math.floor(samples.length / 2)] : null, connect, n: samples.length })
    }
    const timer = window.setTimeout(done, ms)
    ws.onopen = () => {
      connect = Math.round(performance.now() - t0)
      if (p.sub) ws.send(JSON.stringify(p.sub))
    }
    ws.onmessage = (e) => {
      try {
        const m = JSON.parse(e.data)
        const ts = p.ts(m)
        if (ts) samples.push(Math.max(0, Date.now() - ts))
        if (samples.length >= 40) {
          clearTimeout(timer)
          done()
        }
      } catch {
        /* pong / non-json */
      }
    }
    ws.onerror = () => {
      clearTimeout(timer)
      done()
    }
  })
}

let probing: Promise<void> | null = null
/** Measure all venues in parallel (5s). Cached in localStorage for 20 min unless forced. */
export function probeVenues(force = false) {
  if (!force && Date.now() - venueLat.t < PROBE_TTL && Object.keys(venueLat.res).length) return Promise.resolve()
  if (probing) return probing
  probing = (async () => {
    const names = Object.keys(PROBES)
    const res = await Promise.all(names.map((n) => probeOne(n)))
    venueLat = { t: Date.now(), res: Object.fromEntries(names.map((n, i) => [n, res[i]])) }
    localStorage.setItem(LAT_KEY, JSON.stringify(venueLat))
    latSubs.forEach((f) => f())
  })().finally(() => {
    probing = null
  })
  return probing
}

/** Order candidate venues by measured latency; within 25% of each other the original (backend) order wins. */
export function rankVenues(cands: string[]): string[] {
  return rankBy(cands, venueLat.res)
}

/** Pure ranking: unmeasured venues sit at the median; log-1.25 buckets give the hysteresis. */
export function rankBy(cands: string[], lat: Record<string, VenueLat | undefined>): string[] {
  const cost = (v: string) => lat[v]?.ms ?? null
  const known = cands.map(cost).filter((x): x is number => x != null).sort((a, b) => a - b)
  if (!known.length) return cands.filter((v) => !(lat[v] && lat[v]!.ms == null)).concat(cands.filter((v) => lat[v] && lat[v]!.ms == null))
  const fill = known[Math.floor(known.length / 2)]
  // measured-but-failed (ms null) goes last; never measured sits at the median
  const c = (v: string) => (lat[v] && lat[v]!.ms == null ? Infinity : cost(v) ?? fill)
  // greedy hysteresis: take the earliest venue within 25% of the fastest remaining one
  const rest = [...cands]
  const out: string[] = []
  while (rest.length) {
    const best = Math.min(...rest.map(c))
    const i = Number.isFinite(best) ? rest.findIndex((v) => c(v) <= best * 1.25) : 0
    out.push(rest.splice(i, 1)[0])
  }
  return out
}

function chunk<T>(a: T[], n: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < a.length; i += n) out.push(a.slice(i, i + n))
  return out
}

const BINANCE_BASE = { binance: 'wss://stream.binance.com:9443/stream', 'binance.vision': 'wss://data-stream.binance.vision/stream' }
function binanceUrls() {
  return rankVenues(['binance', 'binance.vision']).map((v) => BINANCE_BASE[v as keyof typeof BINANCE_BASE])
}

class Sock {
  ws: WebSocket | null = null
  urlIdx = 0
  backoff = 500
  pingId = 0
  closed = false
  urls: string[]
  onOpen: (ws: WebSocket) => void
  onMsg: (d: any) => void
  ping?: string
  constructor(urls: string[], onOpen: (ws: WebSocket) => void, onMsg: (d: any) => void, ping?: string) {
    this.urls = urls
    this.onOpen = onOpen
    this.onMsg = onMsg
    this.ping = ping
    this.connect()
  }
  connect() {
    if (this.closed) return
    const url = this.urls[this.urlIdx % this.urls.length]
    const ws = new WebSocket(url)
    this.ws = ws
    const t = window.setTimeout(() => ws.readyState !== 1 && ws.close(), 6000)
    ws.onopen = () => {
      clearTimeout(t)
      this.backoff = 500
      this.onOpen(ws)
      if (this.ping) this.pingId = window.setInterval(() => ws.readyState === 1 && ws.send(this.ping!), 20000)
    }
    ws.onmessage = (e) => {
      if (e.data === 'pong') return
      try {
        this.onMsg(JSON.parse(e.data))
      } catch {
        /* ignore */
      }
    }
    ws.onclose = () => {
      clearInterval(this.pingId)
      if (this.closed) return
      this.urlIdx++ // rotate to the fallback endpoint
      window.setTimeout(() => this.connect(), this.backoff)
      this.backoff = Math.min(this.backoff * 2, 15000)
    }
    ws.onerror = () => ws.close()
  }
  send(o: unknown) {
    if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(o))
  }
  close() {
    this.closed = true
    clearInterval(this.pingId)
    this.ws?.close()
  }
  get open() {
    return this.ws?.readyState === 1
  }
}

class Live {
  prices = new Map<string, Tick>()
  subs = new Map<string, Set<Cb>>()
  venue = new Map<string, { venue: Venue; pair: string }>()
  pending = new Map<string, Promise<void>>()
  bn: Sock | null = null
  bb: Sock | null = null
  ok: Sock | null = null
  bnStreams = new Set<string>()
  bbTopics = new Set<string>()
  okArgs = new Set<string>()
  status: Record<Venue, boolean> = { binance: false, bybit: false, okx: false }
  lastMsg = 0
  retries = new Map<string, number>()
  // Subscriptions are coalesced: Binance closes a connection that receives more than 5 control messages per second
  // (code 1008 "Too many requests", after ~6s of silence), which a burst of one SUBSCRIBE per symbol triggers.
  q: Record<Venue, Set<string>> = { binance: new Set(), bybit: new Set(), okx: new Set() }
  qTimer = 0

  queue(venue: Venue, item: string) {
    this.q[venue].add(item)
    if (!this.qTimer) this.qTimer = window.setTimeout(() => this.flush(), 250)
  }

  flush() {
    this.qTimer = 0
    // anything queued while a socket is still connecting is already in its set and goes out in the onOpen batch
    const b = [...this.q.binance]
    if (b.length && this.bn?.open) this.bn.send({ method: 'SUBSCRIBE', params: b, id: Date.now() % 1e6 })
    for (const args of chunk([...this.q.bybit], 10)) if (this.bb?.open) this.bb.send({ op: 'subscribe', args })
    const o = [...this.q.okx]
    if (o.length && this.ok?.open) this.ok.send({ op: 'subscribe', args: o.map((i) => ({ channel: 'tickers', instId: i })) })
    for (const k of Object.keys(this.q) as Venue[]) this.q[k].clear()
  }

  async resolve(sym: string) {
    if (this.venue.has(sym) || sym.length > 15) return // on-chain mints are streamed by dexStream, not CEX sockets
    if (!this.pending.has(sym)) {
      this.pending.set(
        sym,
        api(`/api/crypto/resolve?sym=${encodeURIComponent(sym)}`)
          .then((r) => {
            const cands: { venue: Venue; pair: string }[] = r.venues?.length ? r.venues : [{ venue: r.venue, pair: r.pair }]
            const best = rankVenues(cands.map((c) => c.venue))[0]
            const pick = cands.find((c) => c.venue === best) || cands[0]
            this.venue.set(sym, { venue: pick.venue, pair: pick.pair })
            this.retries.delete(sym)
          })
          .catch(() => {
            // Never cache a failure (backend restarting, cold start, network blip): forget it and retry with backoff
            // while something is still subscribed, otherwise the symbol would stay dead until a page reload.
            this.pending.delete(sym)
            const n = (this.retries.get(sym) || 0) + 1
            this.retries.set(sym, n)
            if (n <= 8) window.setTimeout(() => this.subs.get(sym)?.size && this.watch(sym), Math.min(30000, 1000 * 2 ** n))
          }),
      )
    }
    await this.pending.get(sym)
  }

  emit(t: Tick) {
    this.prices.set(t.sym, t)
    this.lastMsg = Date.now()
    this.subs.get(t.sym)?.forEach((cb) => cb(t))
    this.subs.get('*')?.forEach((cb) => cb(t))
  }

  ensureBinance() {
    if (this.bn) return
    this.bn = new Sock(
      binanceUrls(),
      (ws) => {
        this.status.binance = true
        if (this.bnStreams.size) ws.send(JSON.stringify({ method: 'SUBSCRIBE', params: [...this.bnStreams], id: 1 }))
      },
      (m) => {
        const d = m.data
        if (!d || d.e !== '24hrMiniTicker') return
        const sym = (d.s as string).replace(/USDT$/, '')
        const c = +d.c
        const o = +d.o
        this.emit({ sym, price: c, chg24: o ? (c / o - 1) * 100 : undefined, ts: d.E, venue: 'binance' })
      },
    )
  }

  ensureBybit() {
    if (this.bb) return
    this.bb = new Sock(
      ['wss://stream.bybit.com/v5/public/spot'],
      (ws) => {
        this.status.bybit = true
        for (const args of chunk([...this.bbTopics], 10)) ws.send(JSON.stringify({ op: 'subscribe', args })) // Bybit: max 10 per request
      },
      (m) => {
        if (!m.topic?.startsWith('tickers.') || !m.data) return
        const d = m.data
        const sym = (d.symbol as string).replace(/USDT$/, '')
        this.emit({ sym, price: +d.lastPrice, chg24: +d.price24hPcnt * 100, ts: m.ts, venue: 'bybit' })
      },
      JSON.stringify({ op: 'ping' }),
    )
  }

  ensureOkx() {
    if (this.ok) return
    this.ok = new Sock(
      ['wss://ws.okx.com:8443/ws/v5/public'],
      (ws) => {
        this.status.okx = true
        if (this.okArgs.size)
          ws.send(JSON.stringify({ op: 'subscribe', args: [...this.okArgs].map((i) => ({ channel: 'tickers', instId: i })) }))
      },
      (m) => {
        if (m.arg?.channel !== 'tickers' || !m.data) return
        const d = m.data[0]
        const sym = (d.instId as string).replace(/-USDT$/, '')
        const last = +d.last
        const o = +d.open24h
        this.emit({ sym, price: last, chg24: o ? (last / o - 1) * 100 : undefined, ts: +d.ts, venue: 'okx' })
      },
      'ping',
    )
  }

  async watch(sym: string) {
    await this.resolve(sym)
    const v = this.venue.get(sym)
    if (!v) return
    if (v.venue === 'binance') {
      const s = `${v.pair.toLowerCase()}@miniTicker`
      if (!this.bnStreams.has(s)) {
        this.bnStreams.add(s)
        this.ensureBinance()
        this.queue('binance', s)
      }
    } else if (v.venue === 'bybit') {
      const t = `tickers.${v.pair}`
      if (!this.bbTopics.has(t)) {
        this.bbTopics.add(t)
        this.ensureBybit()
        this.queue('bybit', t)
      }
    } else {
      if (!this.okArgs.has(v.pair)) {
        this.okArgs.add(v.pair)
        this.ensureOkx()
        this.queue('okx', v.pair)
      }
    }
  }

  subscribe(sym: string, cb: Cb) {
    if (!this.subs.has(sym)) this.subs.set(sym, new Set())
    this.subs.get(sym)!.add(cb)
    if (sym !== '*') this.watch(sym)
    const last = this.prices.get(sym)
    if (last) cb(last)
    return () => {
      this.subs.get(sym)?.delete(cb)
    }
  }

  connected() {
    return !!(this.bn?.open || this.bb?.open || this.ok?.open)
  }
}

export const live = new Live()

// ------------------------------------------------------------------ kline stream (chart)
export type Bar = { t: number; o: number; h: number; l: number; c: number; v: number; closed?: boolean; E?: number }

const BYBIT_IV: Record<string, string> = { '1m': '1', '5m': '5', '15m': '15', '30m': '30', '1h': '60', '4h': '240', '1d': 'D', '1w': 'W' }
const OKX_IV: Record<string, string> = { '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1H', '4h': '4H', '1d': '1Dutc', '1w': '1Wutc' }

export function klineStream(sym: string, interval: string, onBar: (b: Bar) => void, onState?: (s: string) => void) {
  let sock: Sock | null = null
  let poll = 0
  let stopped = false
  ;(async () => {
    await live.resolve(sym)
    if (stopped) return
    const v = live.venue.get(sym)
    if (!v) {
      startPoll()
      return
    }
    if (v.venue === 'binance') {
      const s = `${v.pair.toLowerCase()}@kline_${interval}`
      sock = new Sock(
        binanceUrls().map((u) => u.replace(/\/stream$/, `/ws/${s}`)),
        () => onState?.('binance'),
        (m) => {
          const k = m.k
          if (!k) return
          onBar({ t: k.t / 1000, o: +k.o, h: +k.h, l: +k.l, c: +k.c, v: +k.q, closed: k.x, E: m.E })
        },
      )
    } else if (v.venue === 'bybit') {
      const topic = `kline.${BYBIT_IV[interval]}.${v.pair}`
      sock = new Sock(
        ['wss://stream.bybit.com/v5/public/spot'],
        (ws) => {
          onState?.('bybit')
          ws.send(JSON.stringify({ op: 'subscribe', args: [topic] }))
        },
        (m) => {
          if (m.topic !== topic) return
          for (const k of m.data)
            onBar({ t: k.start / 1000, o: +k.open, h: +k.high, l: +k.low, c: +k.close, v: +k.turnover, closed: k.confirm, E: m.ts })
        },
        JSON.stringify({ op: 'ping' }),
      )
    } else {
      const ch = `candle${OKX_IV[interval]}`
      sock = new Sock(
        ['wss://ws.okx.com:8443/ws/v5/business'],
        (ws) => {
          onState?.('okx')
          ws.send(JSON.stringify({ op: 'subscribe', args: [{ channel: ch, instId: v.pair }] }))
        },
        (m) => {
          if (m.arg?.channel !== ch || !m.data) return
          for (const k of m.data)
            onBar({ t: +k[0] / 1000, o: +k[1], h: +k[2], l: +k[3], c: +k[4], v: +k[7], closed: k[8] === '1', E: +k[0] })
        },
        'ping',
      )
    }
    // safety net: if the socket stays silent, poll the backend
    window.setTimeout(() => {
      if (!stopped && !sock?.open) startPoll()
    }, 8000)
  })()

  function startPoll() {
    onState?.('poll')
    poll = window.setInterval(async () => {
      try {
        const d = await api(`/api/crypto/klines?sym=${sym}&interval=${interval}&limit=2`)
        const r = d.rows[d.rows.length - 1]
        if (r) onBar({ ...r })
      } catch {
        /* keep trying */
      }
    }, 2000)
  }

  return () => {
    stopped = true
    sock?.close()
    clearInterval(poll)
  }
}

// ------------------------------------------------------------------ order book + trades (Binance / Bybit)
export type Book = { bids: [number, number][]; asks: [number, number][]; E?: number }
export type Trade = { p: number; q: number; buy: boolean; t: number }

export function bookStream(sym: string, onBook: (b: Book) => void, onTrade: (t: Trade) => void) {
  let sock: Sock | null = null
  let stopped = false
  const bb = { bids: new Map<number, number>(), asks: new Map<number, number>() }
  ;(async () => {
    await live.resolve(sym)
    if (stopped) return
    const v = live.venue.get(sym)
    if (!v) return
    if (v.venue === 'binance') {
      const p = v.pair.toLowerCase()
      sock = new Sock(
        binanceUrls().map((u) => `${u}?streams=${p}@depth20@100ms/${p}@aggTrade`),
        () => undefined,
        (m) => {
          const d = m.data
          if (!d) return
          if (m.stream.endsWith('@aggTrade')) onTrade({ p: +d.p, q: +d.q, buy: !d.m, t: d.T })
          else onBook({ bids: d.bids.map((x: string[]) => [+x[0], +x[1]]), asks: d.asks.map((x: string[]) => [+x[0], +x[1]]) })
        },
      )
    } else if (v.venue === 'bybit') {
      sock = new Sock(
        ['wss://stream.bybit.com/v5/public/spot'],
        (ws) => ws.send(JSON.stringify({ op: 'subscribe', args: [`orderbook.50.${v.pair}`, `publicTrade.${v.pair}`] })),
        (m) => {
          if (m.topic?.startsWith('publicTrade')) {
            for (const t of m.data) onTrade({ p: +t.p, q: +t.v, buy: t.S === 'Buy', t: t.T })
          } else if (m.topic?.startsWith('orderbook')) {
            if (m.type === 'snapshot') {
              bb.bids.clear()
              bb.asks.clear()
            }
            for (const [p, q] of m.data.b) {
              if (+q === 0) bb.bids.delete(+p)
              else bb.bids.set(+p, +q)
            }
            for (const [p, q] of m.data.a) {
              if (+q === 0) bb.asks.delete(+p)
              else bb.asks.set(+p, +q)
            }
            onBook({
              bids: [...bb.bids.entries()].sort((a, b) => b[0] - a[0]).slice(0, 20),
              asks: [...bb.asks.entries()].sort((a, b) => a[0] - b[0]).slice(0, 20),
            })
          }
        },
        JSON.stringify({ op: 'ping' }),
      )
    }
  })()
  return () => {
    stopped = true
    sock?.close()
  }
}

// ------------------------------------------------------------------ DEX-only tokens (no public WS: poll the backend)
const IV_SEC: Record<string, number> = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 }

/** Live bar from Jupiter prices (via backend, ~3s), continuing the last historical candle. */
export function dexStream(mint: string, interval: string, last: Bar | undefined, onBar: (b: Bar) => void, onState?: (s: string) => void) {
  let cur: Bar | undefined = last ? { ...last } : undefined
  const iv = IV_SEC[interval] || 3600
  onState?.('jupiter · poll 3s')
  const tick = async () => {
    if (document.visibilityState !== 'visible') return
    try {
      const d = await api(`/api/dex/prices?mints=${mint}`)
      const p = d?.[mint]?.price
      if (!p) return
      const now = Date.now() / 1000
      const bt = Math.floor(now / iv) * iv
      if (!cur || bt > cur.t) cur = { t: bt, o: cur?.c ?? p, h: p, l: p, c: p, v: 0 }
      else cur = { ...cur, c: p, h: Math.max(cur.h, p), l: Math.min(cur.l, p) }
      onBar({ ...cur })
      live.emit({ sym: mint, price: p, chg24: d[mint]?.chg24 ?? undefined, ts: Date.now(), venue: 'dex' })
    } catch {
      /* keep polling */
    }
  }
  const id = window.setInterval(tick, 3000)
  tick()
  return () => clearInterval(id)
}

// debugging handle for the local app (inspect live feed state from devtools: __rook.live)
;(window as unknown as { __rook: unknown }).__rook = { live, venueLat: () => venueLat }
