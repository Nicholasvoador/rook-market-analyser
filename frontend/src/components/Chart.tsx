import { useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api } from '../lib/api'
import { decimalsFor } from '../lib/fmt'
import { ema, macd, rsi, type Num } from '../lib/ind'
import { dexStream, klineStream, type Bar } from '../lib/live'

export type Ind = { ema9?: boolean; ema21?: boolean; ema50?: boolean; ema200?: boolean; vol?: boolean; rsi?: boolean; macd?: boolean }
export type ChartInfo = { latency?: number; source?: string; last?: number; feed?: string }
export type PriceLine = { price: number; title: string; color: string }
/** Forecast cone: quantile prices at t0 + h hours, anchored at (t0, price). */
export type Cone = { t0: number; price: number; h: number; q: Record<string, number> }

const IV_SEC: Record<string, number> = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 }
const TZ = -new Date().getTimezoneOffset() * 60 // render in local time
const uiScale = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1
const C = {
  up: '#2fbf88',
  down: '#f2566b',
  ema9: '#8cc8ff',
  ema21: '#ff8a3d',
  ema50: '#e8b74a',
  ema200: '#b9c0cb',
  grid: '#151a20',
  text: '#828c9b',
  line: '#1f252e',
}
const EMAS: [keyof Ind, number, string][] = [
  ['ema9', 9, C.ema9],
  ['ema21', 21, C.ema21],
  ['ema50', 50, C.ema50],
  ['ema200', 200, C.ema200],
]

const ts = (t: number) => (t + TZ) as UTCTimestamp

export default function Chart({
  sym,
  kind,
  interval,
  ind = { ema9: true, ema21: true, ema50: true, ema200: true, vol: true },
  height = '100%',
  compact = false,
  priceLines = [],
  cone = null,
  onInfo,
}: {
  sym: string
  kind: 'crypto' | 'stock' | 'dex'
  interval: string
  ind?: Ind
  height?: number | string
  compact?: boolean
  priceLines?: PriceLine[]
  cone?: Cone | null
  onInfo?: (i: ChartInfo) => void
}) {
  const el = useRef<HTMLDivElement>(null)
  const infoRef = useRef(onInfo)
  infoRef.current = onInfo
  const plKey = JSON.stringify(priceLines)
  const coneKey = cone ? `${cone.t0}:${cone.h}:${cone.q['10']}:${cone.q['90']}` : ''
  const scale = uiScale()

  useEffect(() => {
    if (!el.current) return
    let disposed = false
    const chart: IChartApi = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: C.text,
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: Math.round((compact ? 11 : 12) * scale),
        attributionLogo: false,
        panes: { separatorColor: C.line, separatorHoverColor: '#2a313c' },
      },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line, timeVisible: !['1d', '1w'].includes(interval), secondsVisible: false, rightOffset: 4 },
    })
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: C.up,
      downColor: C.down,
      borderVisible: false,
      wickUpColor: C.up,
      wickDownColor: C.down,
    })
    const vol = ind.vol
      ? chart.addSeries(HistogramSeries, { priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false })
      : null
    if (vol) chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    const emaS: [number, ISeriesApi<'Line'>][] = EMAS.filter(([k]) => ind[k]).map(([, n, color]) => [
      n,
      chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
    ])
    let pane = 1
    const rsiS = ind.rsi ? chart.addSeries(LineSeries, { color: C.ema9, lineWidth: 1, priceLineVisible: false }, pane++) : null
    if (rsiS) {
      rsiS.createPriceLine({ price: 70, color: '#3a2228', lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' })
      rsiS.createPriceLine({ price: 30, color: '#1f3a2e', lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' })
    }
    const macdPane = ind.macd ? pane++ : -1
    const macdH = ind.macd ? chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, macdPane) : null
    const macdL = ind.macd ? chart.addSeries(LineSeries, { color: C.ema200, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, macdPane) : null
    const macdS = ind.macd ? chart.addSeries(LineSeries, { color: C.ema21, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, macdPane) : null
    const panes = chart.panes()
    if (panes.length > 1) {
      panes[0].setStretchFactor(4)
      panes.slice(1).forEach((p) => p.setStretchFactor(1))
    }
    for (const pl of priceLines)
      candle.createPriceLine({ price: pl.price, color: pl.color, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: pl.title })

    // forecast cone: 10/25/50/75/90% lines from the issue bar to the horizon, widening with sqrt(time)
    const ivs = IV_SEC[interval]
    const coneS =
      cone && ivs
        ? ([
            ['90', 'rgba(140,200,255,.85)', LineStyle.Dashed, '90%'],
            ['75', 'rgba(140,200,255,.35)', LineStyle.Dotted, ''],
            ['50', 'rgba(255,138,61,.75)', LineStyle.Dotted, 'median'],
            ['25', 'rgba(140,200,255,.35)', LineStyle.Dotted, ''],
            ['10', 'rgba(140,200,255,.85)', LineStyle.Dashed, '10%'],
          ] as const)
            .filter(([k]) => cone.q[k])
            .map(([k, color, style, title]) => ({
              k,
              s: chart.addSeries(LineSeries, {
                color,
                lineWidth: 1,
                lineStyle: style,
                priceLineVisible: false,
                lastValueVisible: !!title,
                title,
                crosshairMarkerVisible: false,
              }),
            }))
        : []
    const drawCone = () => {
      if (!cone || !ivs || !coneS.length) return 0
      const anchor = Math.floor(cone.t0 / ivs) * ivs
      const K = Math.min(Math.ceil((cone.h * 3600) / ivs), 400)
      for (const { k, s } of coneS) {
        const lr = Math.log(cone.q[k] / cone.price)
        s.setData(Array.from({ length: K + 1 }, (_, i) => ({ time: ts(anchor + i * ivs), value: cone.price * Math.exp(lr * Math.sqrt(i / K)) })))
      }
      return Math.max(0, Math.ceil((anchor + K * ivs - (bars.at(-1)?.t ?? anchor)) / ivs))
    }

    let bars: Bar[] = []
    const line = (arr: Num[]) =>
      arr.flatMap((v, i) => (v == null ? [] : [{ time: ts(bars[i].t), value: v }]))

    const setAll = () => {
      const c = bars.map((b) => b.c)
      candle.setData(bars.map((b) => ({ time: ts(b.t), open: b.o, high: b.h, low: b.l, close: b.c })))
      const d = decimalsFor(c[c.length - 1] || 1)
      candle.applyOptions({ priceFormat: { type: 'price', precision: d, minMove: 1 / 10 ** d } })
      vol?.setData(bars.map((b) => ({ time: ts(b.t), value: b.v, color: b.c >= b.o ? 'rgba(47,191,136,.35)' : 'rgba(242,86,107,.35)' })))
      for (const [n, s] of emaS) s.setData(line(ema(c, n)))
      if (rsiS) rsiS.setData(line(rsi(c)))
      if (macdH && macdL && macdS) {
        const m = macd(c)
        macdL.setData(line(m.m))
        macdS.setData(line(m.signal))
        macdH.setData(m.hist.flatMap((v, i) => (v == null ? [] : [{ time: ts(bars[i].t), value: v, color: v >= 0 ? 'rgba(47,191,136,.55)' : 'rgba(242,86,107,.55)' }])))
      }
    }

    // incremental: candle updates immediately; indicators coalesce to one recompute per frame
    let raf = 0
    const updateInd = () => {
      raf = 0
      if (!bars.length) return
      const c = bars.map((b) => b.c)
      const i = bars.length - 1
      const t = ts(bars[i].t)
      for (const [n, s] of emaS) {
        const v = ema(c.slice(-Math.max(n * 4, 400)), n).at(-1)
        if (v != null) s.update({ time: t, value: v })
      }
      if (rsiS) {
        const v = rsi(c.slice(-400)).at(-1)
        if (v != null) rsiS.update({ time: t, value: v })
      }
      if (macdH && macdL && macdS) {
        const m = macd(c.slice(-400))
        const h = m.hist.at(-1)
        if (m.m.at(-1) != null) macdL.update({ time: t, value: m.m.at(-1)! })
        if (m.signal.at(-1) != null) macdS.update({ time: t, value: m.signal.at(-1)! })
        if (h != null) macdH.update({ time: t, value: h, color: h >= 0 ? 'rgba(47,191,136,.55)' : 'rgba(242,86,107,.55)' })
      }
    }
    let lastInfo = 0
    const onBar = (b: Bar) => {
      if (disposed || !bars.length) return
      const last = bars[bars.length - 1]
      if (b.t < last.t) return
      if (b.t === last.t) bars[bars.length - 1] = b
      else {
        bars.push(b)
        if (bars.length > 1500) bars = bars.slice(-1200)
      }
      candle.update({ time: ts(b.t), open: b.o, high: b.h, low: b.l, close: b.c })
      vol?.update({ time: ts(b.t), value: b.v, color: b.c >= b.o ? 'rgba(47,191,136,.35)' : 'rgba(242,86,107,.35)' })
      if (!raf) raf = requestAnimationFrame(updateInd)
      const now = performance.now()
      if (now - lastInfo > 250) {
        lastInfo = now
        infoRef.current?.({ last: b.c, latency: b.E && b.E > 1e12 ? Math.max(0, Date.now() - b.E) : undefined })
      }
    }

    let stop: (() => void) | undefined
    ;(async () => {
      try {
        const d =
          kind === 'crypto'
            ? await api(`/api/crypto/klines?sym=${encodeURIComponent(sym)}&interval=${interval}&limit=1000`)
            : kind === 'dex'
              ? await api(`/api/dex/klines?mint=${encodeURIComponent(sym)}&interval=${interval}&limit=1000`)
              : await api(`/api/stocks/klines?sym=${encodeURIComponent(sym)}&interval=${interval}`)
        if (disposed) return
        bars = d.rows
        setAll()
        const ahead = drawCone()
        chart.timeScale().fitContent()
        if (bars.length > 160)
          chart.timeScale().setVisibleLogicalRange({ from: bars.length - (compact ? 120 : 180), to: bars.length + 4 + Math.min(ahead, 60) })
        infoRef.current?.({ source: d.source, last: bars.at(-1)?.c })
        if (kind === 'crypto') {
          stop = klineStream(sym, interval, onBar, (feed) => infoRef.current?.({ feed }))
        } else if (kind === 'dex') {
          stop = dexStream(sym, interval, bars.at(-1), onBar, (feed) => infoRef.current?.({ feed }))
        } else {
          infoRef.current?.({ feed: 'poll 15s' })
          const id = window.setInterval(async () => {
            if (document.visibilityState !== 'visible') return
            try {
              const r = await api(`/api/stocks/klines?sym=${encodeURIComponent(sym)}&interval=${interval}`)
              for (const b of r.rows.slice(-3)) onBar(b)
            } catch {
              /* keep last */
            }
          }, 15000)
          stop = () => clearInterval(id)
        }
      } catch (e) {
        infoRef.current?.({ source: `error: ${(e as Error).message}` })
      }
    })()

    return () => {
      disposed = true
      stop?.()
      cancelAnimationFrame(raf)
      chart.remove()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sym, kind, interval, ind.ema9, ind.ema21, ind.ema50, ind.ema200, ind.vol, ind.rsi, ind.macd, compact, plKey, coneKey, scale])

  return <div ref={el} style={{ height, width: '100%', minHeight: '7.5rem' }} />
}
