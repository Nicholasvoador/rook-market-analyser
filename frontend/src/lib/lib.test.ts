// @vitest-environment jsdom
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { GLOSSARY } from './glossary'
import { big, decimalsFor, pct, price, tone } from './fmt'
import { rankBy } from './live'

describe('rankBy (latency-first venue choice)', () => {
  const lat = (ms: number | null) => ({ ms, connect: null, n: 5 })

  it('puts the fastest venue first', () => {
    expect(rankBy(['binance', 'bybit', 'okx'], { binance: lat(180), bybit: lat(40), okx: lat(400) })).toEqual(['bybit', 'binance', 'okx'])
  })

  it('keeps the backend order when venues are within 25% of each other (no flapping)', () => {
    expect(rankBy(['binance', 'bybit'], { binance: lat(110), bybit: lat(100) })).toEqual(['binance', 'bybit'])
    expect(rankBy(['bybit', 'binance'], { binance: lat(110), bybit: lat(100) })).toEqual(['bybit', 'binance'])
  })

  it('places unmeasured venues at the median, and failed ones too', () => {
    const r = rankBy(['new', 'slow', 'fast', 'dead'], { slow: lat(900), fast: lat(30), dead: lat(null) })
    expect(r).toEqual(['fast', 'new', 'slow', 'dead'])
  })

  it('a faster venue only overtakes when it is more than 25% faster', () => {
    expect(rankBy(['binance', 'okx'], { binance: lat(124), okx: lat(100) })).toEqual(['binance', 'okx'])
    expect(rankBy(['binance', 'okx'], { binance: lat(126), okx: lat(100) })).toEqual(['okx', 'binance'])
  })

  it('returns the input order when nothing is measured', () => {
    expect(rankBy(['a', 'b', 'c'], {})).toEqual(['a', 'b', 'c'])
  })
})

describe('formatting', () => {
  it('picks sensible decimals for prices across magnitudes', () => {
    expect(decimalsFor(65000)).toBeLessThanOrEqual(2)
    expect(decimalsFor(0.00002)).toBeGreaterThanOrEqual(6)
    expect(price(null)).toBe('—')
  })
  it('signs percentages and compacts big numbers', () => {
    expect(pct(1.234)).toBe('+1.23%')
    expect(pct(-0.5, 1)).toBe('-0.5%')
    expect(big(1_500_000)).toMatch(/^1\.5\d*M$/)
    expect(big(2_000_000_000, 1)).toBe('2.0B')
  })
  it('tone never relies on sign alone being visible (returns class names)', () => {
    expect(tone(1)).toBe('up')
    expect(tone(-1)).toBe('down')
    expect(tone(0)).toBe('')
    expect(tone(NaN)).toBe('')
  })
})

describe('glossary coverage', () => {
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((f) => {
      const p = join(d, f)
      return statSync(p).isDirectory() ? walk(p) : p.endsWith('.tsx') ? [p] : []
    })
  const src = join(__dirname, '..')
  const used = new Set(walk(src).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/<Term k="([a-z0-9_]+)"/g)].map((m) => m[1])))

  it('every <Term k="…"> used in the UI has a plain-language explanation', () => {
    expect(used.size).toBeGreaterThan(10)
    const missing = [...used].filter((k) => !GLOSSARY[k])
    expect(missing).toEqual([])
  })

  it('explanations are short enough to read in a tooltip', () => {
    for (const [k, [title, text]] of Object.entries(GLOSSARY)) {
      expect(title.length, k).toBeGreaterThan(1)
      expect(text.length, k).toBeLessThan(320)
      expect(text.length, k).toBeGreaterThan(20)
    }
  })
})
