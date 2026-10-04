export const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

export function decimalsFor(p: number) {
  const a = Math.abs(p)
  if (a >= 1000) return 2
  if (a >= 100) return 2
  if (a >= 1) return 3
  if (a >= 0.01) return 5
  if (a >= 0.0001) return 7
  return 9
}

export function price(x: unknown, d?: number) {
  if (!isNum(x)) return '—'
  const dd = d ?? decimalsFor(x)
  return x.toLocaleString('en-US', { minimumFractionDigits: Math.min(dd, 2), maximumFractionDigits: dd })
}

export function num(x: unknown, d = 2) {
  if (!isNum(x)) return '—'
  return x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
}

export function pct(x: unknown, d = 2, sign = true) {
  if (!isNum(x)) return '—'
  return `${sign && x > 0 ? '+' : ''}${x.toFixed(d)}%`
}

export function big(x: unknown, d = 2) {
  if (!isNum(x)) return '—'
  const a = Math.abs(x)
  const s = x < 0 ? '-' : ''
  if (a >= 1e12) return `${s}${(a / 1e12).toFixed(d)}T`
  if (a >= 1e9) return `${s}${(a / 1e9).toFixed(d)}B`
  if (a >= 1e6) return `${s}${(a / 1e6).toFixed(d)}M`
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(d === 2 ? 1 : d)}K`
  return `${s}${a.toFixed(d)}`
}

export function money(usd: unknown, ccy: string, rate: number, compact = false) {
  if (!isNum(usd)) return '—'
  const v = ccy === 'BRL' ? usd * rate : usd
  const sym = ccy === 'BRL' ? 'R$' : '$'
  return compact ? `${sym}${big(v)}` : `${sym}${price(v)}`
}

export function ago(ts: number) {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

export function until(ts: number) {
  const s = ts - Date.now() / 1000
  if (s <= 0) return 'due'
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`
  return `${(s / 86400).toFixed(1)}d`
}

export const tone = (x: unknown) => (!isNum(x) || x === 0 ? '' : x > 0 ? 'up' : 'down')

export function clock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}
