import { describe, expect, it } from 'vitest'
import { backdrop, fmtRet, periodStart, perfLines, ret, type MRow, type Perf } from './markets'

const row = (o: Partial<MRow>): MRow => ({ sym: 'X', label: 'X', kind: 'equity', ...o }) as MRow

describe('ret (return modes)', () => {
  it('uses BRL / CDI values when available', () => {
    const r = row({ m1: 5, brl: { m1: 7 }, xcdi: { m1: 6 } })
    expect(ret(r, 'm1', 'local')).toEqual({ v: 5, local: false })
    expect(ret(r, 'm1', 'brl')).toEqual({ v: 7, local: false })
    expect(ret(r, 'm1', 'xcdi')).toEqual({ v: 6, local: false })
  })
  it('falls back to own currency and flags it (FX, EUR/JPY assets)', () => {
    expect(ret(row({ m1: 2, brl: null }), 'm1', 'brl')).toEqual({ v: 2, local: true })
  })
  it('yields always report their own change', () => {
    expect(ret(row({ unit: 'yield', m1: 25, brl: { m1: 1 } }), 'm1', 'brl')).toEqual({ v: 25, local: false })
  })
})

describe('fmtRet', () => {
  it('formats percentages and basis points', () => {
    expect(fmtRet(1.234)).toBe('+1.2%')
    expect(fmtRet(-12.4, 'yield')).toBe('-12 bp')
    expect(fmtRet(48.6, 'yield')).toBe('+49 bp')
    expect(fmtRet(null)).toBe('—')
  })
})

describe('periodStart', () => {
  const dates = ['2025-12-30', '2025-12-31', '2026-01-02', '2026-02-02', '2026-09-03', '2026-10-02']
  const today = new Date('2026-10-04T12:00:00Z')
  it('YTD starts at the last close of the previous year', () => {
    expect(periodStart(dates, 'ytd', today)).toBe(1)
  })
  it('1M starts at the last close on or before 30 days ago', () => {
    expect(periodStart(dates, 'm1', today)).toBe(4)
  })
})

describe('perfLines', () => {
  const perf: Perf = {
    dates: ['2025-12-31', '2026-06-30', '2026-10-02'],
    series: { 'BTC-USD': [100, 150, 120], '^BVSP': [1000, 1100, 1210] },
    ccy: { 'BTC-USD': 'USD', '^BVSP': 'BRL' },
    labels: { 'BTC-USD': 'Bitcoin', '^BVSP': 'Ibovespa' },
    usdbrl: [5, 5.5, 5.5],
    cdi: [1, 1.07, 1.1],
  }
  const today = new Date('2026-10-04T00:00:00Z')

  it('rebases to 0% at the period start, in USD', () => {
    const l = perfLines(perf, 'ytd', 'USD', today)
    const btc = l.find((x) => x.key === 'BTC-USD')!
    expect(btc.points[0].value).toBe(0)
    expect(btc.last).toBeCloseTo(20)
    // Ibovespa converted to USD: 1210/5.5 vs 1000/5
    expect(l.find((x) => x.key === '^BVSP')!.last).toBeCloseTo((1210 / 5.5 / (1000 / 5) - 1) * 100)
    expect(l.find((x) => x.key === 'CDI')).toBeUndefined()
  })

  it('in BRL converts USD assets with USD/BRL and adds the CDI benchmark', () => {
    const l = perfLines(perf, 'ytd', 'BRL', today)
    expect(l.find((x) => x.key === 'BTC-USD')!.last).toBeCloseTo(((120 * 5.5) / (100 * 5) - 1) * 100)
    expect(l.find((x) => x.key === '^BVSP')!.last).toBeCloseTo(21)
    expect(l.find((x) => x.key === 'CDI')!.last).toBeCloseTo(10)
  })
})

describe('backdrop', () => {
  it('summarises tones honestly', () => {
    expect(backdrop([{ tone: 'bull' }, { tone: 'bull' }, { tone: 'neutral' }])).toMatch(/supportive/)
    expect(backdrop([{ tone: 'bear' }, { tone: 'bear' }, { tone: 'bull' }, { tone: 'bear' }])).toMatch(/Headwinds/)
    expect(backdrop([{ tone: 'bull' }, { tone: 'bear' }])).toMatch(/Mixed/)
    expect(backdrop([{ tone: 'neutral' }])).toMatch(/Quiet/)
  })
})
