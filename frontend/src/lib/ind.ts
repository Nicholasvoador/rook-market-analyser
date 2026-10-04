// Indicator math (mirrors backend/rookery/indicators.py) for chart overlays.
export type Num = number | null

export function ema(x: number[], n: number): Num[] {
  const out: Num[] = new Array(x.length).fill(null)
  if (x.length < n) return out
  const a = 2 / (n + 1)
  let s = 0
  for (let i = 0; i < n; i++) s += x[i]
  let prev = s / n
  out[n - 1] = prev
  for (let i = n; i < x.length; i++) {
    prev = a * x[i] + (1 - a) * prev
    out[i] = prev
  }
  return out
}

function wilder(x: number[], n: number): Num[] {
  const out: Num[] = new Array(x.length).fill(null)
  if (x.length <= n) return out
  let s = 0
  for (let i = 1; i <= n; i++) s += x[i]
  let prev = s / n
  out[n] = prev
  for (let i = n + 1; i < x.length; i++) {
    prev = (prev * (n - 1) + x[i]) / n
    out[i] = prev
  }
  return out
}

export function rsi(c: number[], n = 14): Num[] {
  const up: number[] = [0]
  const dn: number[] = [0]
  for (let i = 1; i < c.length; i++) {
    const d = c[i] - c[i - 1]
    up.push(d > 0 ? d : 0)
    dn.push(d < 0 ? -d : 0)
  }
  const u = wilder(up, n)
  const d = wilder(dn, n)
  return u.map((uv, i) => {
    const dv = d[i]
    if (uv == null || dv == null) return null
    if (dv === 0) return 100
    return 100 - 100 / (1 + uv / dv)
  })
}

export function macd(c: number[], f = 12, s = 26, sig = 9) {
  const ef = ema(c, f)
  const es = ema(c, s)
  const m: Num[] = c.map((_, i) => (ef[i] != null && es[i] != null ? (ef[i] as number) - (es[i] as number) : null))
  const first = m.findIndex((v) => v != null)
  const signal: Num[] = new Array(c.length).fill(null)
  if (first >= 0) {
    const seg = ema(m.slice(first) as number[], sig)
    seg.forEach((v, i) => (signal[first + i] = v))
  }
  const hist: Num[] = m.map((v, i) => (v != null && signal[i] != null ? v - (signal[i] as number) : null))
  return { m, signal, hist }
}
