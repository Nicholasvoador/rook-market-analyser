// Pure helpers for the Markets page: return modes, formatting, and the relative-performance series.
import { isNum, pct } from './fmt'

export type Mode = 'local' | 'brl' | 'xcdi'
export type Period = 'm1' | 'm3' | 'ytd' | 'y1'
export const RET_COLS: [string, string][] = [
  ['w1', '1W'],
  ['m1', '1M'],
  ['ytd', 'YTD'],
  ['y1', '1Y'],
]
export const PERIOD_LABEL: Record<string, string> = { w1: '1W', m1: '1M', m3: '3M', ytd: 'YTD', y1: '1Y' }

export type MRow = {
  sym: string
  label: string
  kind: string
  unit?: 'yield' | null
  ccy?: string | null
  price?: number | null
  chg1d?: number | null
  brl?: Record<string, number> | null
  xcdi?: Record<string, number> | null
  [k: string]: any
}

/** A return for the chosen mode. Falls back to the asset's own currency when BRL/CDI doesn't apply (FX, yields,
 * assets quoted in EUR/JPY...): `local` tells the UI to mark it. */
export function ret(r: MRow, k: string, mode: Mode): { v: number | null; local: boolean } {
  const own = isNum(r[k]) ? (r[k] as number) : null
  if (mode === 'local' || r.unit === 'yield') return { v: own, local: false }
  const m = mode === 'brl' ? r.brl : r.xcdi
  if (m && isNum(m[k])) return { v: m[k], local: false }
  return { v: own, local: own != null }
}

export function fmtRet(v: number | null | undefined, unit?: string | null, d = 1) {
  if (!isNum(v)) return '—'
  if (unit === 'yield') return `${v > 0 ? '+' : ''}${v.toFixed(0)} bp`
  return pct(v, d)
}

/** Index of the first point inside the period (the base the series is rebased to). */
export function periodStart(dates: string[], period: Period, today = new Date()): number {
  if (!dates.length) return 0
  let from: string
  if (period === 'ytd') from = `${today.getUTCFullYear() - 1}-12-31`
  else {
    const days = { m1: 30, m3: 91, y1: 365 }[period]
    const d = new Date(today.getTime() - days * 86400000)
    from = d.toISOString().slice(0, 10)
  }
  // last date on or before `from` (so the first move inside the period is counted)
  let i = 0
  for (let j = 0; j < dates.length; j++) if (dates[j] <= from) i = j
  return i
}

export type Perf = {
  dates: string[]
  series: Record<string, (number | null)[]>
  ccy: Record<string, string>
  labels: Record<string, string>
  usdbrl: (number | null)[] | null
  cdi: number[] | null
}

/** Cumulative % change from the period start, optionally converted to BRL (USD assets × USD/BRL) or to USD (BRL
 * assets ÷ USD/BRL). In BRL the CDI line is added as the benchmark. */
export function perfLines(perf: Perf, period: Period, ccy: 'USD' | 'BRL', today = new Date()) {
  const i0 = periodStart(perf.dates, period, today)
  const fx = perf.usdbrl || []
  const conv = (s: string, i: number) => {
    const v = perf.series[s][i]
    if (!isNum(v)) return null
    const own = perf.ccy[s] || 'USD'
    if (own === ccy) return v
    const f = fx[i]
    if (!isNum(f) || !f) return null
    return own === 'USD' ? v * f : v / f
  }
  const out: { key: string; label: string; points: { time: string; value: number }[]; last: number | null }[] = []
  for (const s of Object.keys(perf.series)) {
    let base: number | null = null
    const pts: { time: string; value: number }[] = []
    for (let i = i0; i < perf.dates.length; i++) {
      const v = conv(s, i)
      if (v == null) continue
      if (base == null) base = v
      pts.push({ time: perf.dates[i], value: (v / base - 1) * 100 })
    }
    out.push({ key: s, label: perf.labels[s] || s, points: pts, last: pts.length ? pts[pts.length - 1].value : null })
  }
  if (ccy === 'BRL' && perf.cdi?.length) {
    const b = perf.cdi[i0]
    const pts = perf.dates.slice(i0).map((t, j) => ({ time: t, value: (perf.cdi![i0 + j] / b - 1) * 100 }))
    out.push({ key: 'CDI', label: 'CDI', points: pts, last: pts.length ? pts[pts.length - 1].value : null })
  }
  return out
}

/** One-sentence summary from readout tones (risk-asset point of view). */
export function backdrop(lines: { tone: string }[]) {
  const bull = lines.filter((l) => l.tone === 'bull').length
  const bear = lines.filter((l) => l.tone === 'bear').length
  if (bull >= bear + 2) return 'Backdrop mostly supportive for risk assets.'
  if (bear >= bull + 2) return 'Headwinds outweigh tailwinds for risk assets right now.'
  if (bull === 0 && bear === 0) return 'Quiet backdrop: no strong signal from stocks, the dollar or rates.'
  return 'Mixed backdrop: tailwinds and headwinds roughly balance.'
}

export const SHORT: Record<string, string> = {
  'BTC-USD': 'BTC',
  'ETH-USD': 'ETH',
  '^GSPC': 'S&P',
  '^NDX': 'NDX',
  'GC=F': 'Gold',
  'CL=F': 'Oil',
  'DX-Y.NYB': 'DXY',
  '^TNX': '10Y',
  '^BVSP': 'Ibov',
  'BRL=X': 'USD/BRL',
}

export const KIND_LABEL: Record<string, string> = {
  equity: 'stock',
  etf: 'ETF',
  commodity: 'commodity',
  index: 'index',
  fx: 'FX',
  rate: 'yield',
  crypto: 'crypto',
}

export const TAB_FOR_KIND: Record<string, string> = {
  equity: 'stocks',
  etf: 'etfs',
  commodity: 'commodities',
  index: 'indices',
  fx: 'fx',
  rate: 'rates',
  crypto: 'crypto',
}
