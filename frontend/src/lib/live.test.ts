// @vitest-environment jsdom
// Regression: a burst of one SUBSCRIBE per symbol makes Binance close the socket (1008 "Too many requests") after
// ~6s of silence, which left the ticker tape empty. Subscriptions must be coalesced.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

class FakeWS {
  static all: FakeWS[] = []
  readyState = 0
  sent: any[] = []
  onopen?: () => void
  onmessage?: (e: { data: string }) => void
  onclose?: () => void
  onerror?: () => void
  url: string
  constructor(url: string) {
    this.url = url
    FakeWS.all.push(this)
  }
  send(d: string) {
    if (this.readyState !== 1) throw new Error('send while not open')
    this.sent.push(JSON.parse(d))
  }
  close() {
    this.readyState = 3
    this.onclose?.()
  }
  open() {
    this.readyState = 1
    this.onopen?.()
  }
}

const SYMS = ['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE', 'HYPE', 'SUI', 'LINK', 'AVAX', 'TON', 'ADA']

describe('live feed subscriptions', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeWS.all = []
    vi.stubGlobal('WebSocket', FakeWS)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  it('coalesces a burst of symbols into one SUBSCRIBE message per venue', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const sym = new URL(url, 'http://x').searchParams.get('sym')
      return new Response(JSON.stringify({ venue: 'binance', pair: `${sym}USDT`, venues: [{ venue: 'binance', pair: `${sym}USDT` }] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }))
    const { live } = await import('./live')
    live.subscribe('BTC', () => undefined)
    await vi.waitFor(() => expect(FakeWS.all.some((w) => w.url.includes('binance'))).toBe(true))
    const ws = FakeWS.all.find((w) => w.url.includes('binance'))!
    ws.open() // socket opens BEFORE the other symbols resolve: the worst case that used to burst
    for (const s of SYMS.slice(1)) live.subscribe(s, () => undefined)
    await vi.waitFor(() => expect(live.venue.size).toBe(SYMS.length))
    await vi.advanceTimersByTimeAsync(600)
    const subs = ws.sent.filter((m) => m.method === 'SUBSCRIBE')
    expect(subs.length).toBeLessThanOrEqual(2)
    expect(new Set(subs.flatMap((m) => m.params)).size).toBe(SYMS.length)
  })

  it('a failed resolve is retried instead of cached forever', async () => {
    let calls = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      calls++
      if (calls === 1) return new Response('backend restarting', { status: 502 })
      return new Response(JSON.stringify({ venue: 'binance', pair: 'BTCUSDT', venues: [{ venue: 'binance', pair: 'BTCUSDT' }] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }))
    const { live } = await import('./live')
    live.subscribe('BTC', () => undefined)
    await vi.waitFor(() => expect(calls).toBe(1))
    expect(live.venue.has('BTC')).toBe(false)
    await vi.advanceTimersByTimeAsync(2500) // first retry after 2s
    await vi.waitFor(() => expect(live.venue.get('BTC')?.pair).toBe('BTCUSDT'))
    expect(calls).toBe(2)
  })
})
