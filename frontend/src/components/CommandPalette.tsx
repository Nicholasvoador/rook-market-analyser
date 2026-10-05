import { useEffect, useMemo, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { useApi } from '../lib/api'
import { assetHref, go, useSettings } from '../lib/store'

type Item = { id: string; label: string; hint?: string; act: () => void }

const PAGES: [string, string][] = [
  ['dashboard', 'Dashboard'],
  ['markets/overview', 'Markets · Overview'],
  ['markets/crypto', 'Markets · Crypto'],
  ['markets/stocks', 'Markets · Stocks'],
  ['markets/commodities', 'Markets · Commodities'],
  ['markets/indices', 'Markets · Indices'],
  ['markets/fx', 'Markets · FX'],
  ['markets/rates', 'Markets · Rates'],
  ['markets/etfs', 'Markets · ETFs'],
  ['ask', 'Ask Rook'],
  ['signals', 'Signals scanner'],
  ['predictions', 'Predictions & track record'],
  ['portfolio', 'Portfolio'],
  ['alerts', 'Alerts'],
  ['settings', 'Settings'],
]

export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const inp = useRef<HTMLInputElement>(null)
  const { settings } = useSettings()
  const { data: markets } = useApi<any>(open ? '/api/crypto/markets?n=150' : null)
  const { data: universe } = useApi<any[]>(open ? '/api/markets/universe' : null)

  useEffect(() => {
    if (open) {
      setQ('')
      setSel(0)
      setTimeout(() => inp.current?.focus(), 10)
    }
  }, [open])

  const items = useMemo<Item[]>(() => {
    const s = q.trim()
    const S = s.toUpperCase()
    const out: Item[] = []
    const close = (f: () => void) => () => {
      f()
      onClose()
    }
    for (const [p, l] of PAGES) if (!s || l.toUpperCase().includes(S)) out.push({ id: `p:${p}`, label: l, hint: 'page', act: close(() => go(p)) })
    const rows = markets?.rows || []
    const cr = rows.filter((r: any) => s && (r.sym.startsWith(S) || r.name.toUpperCase().includes(S))).slice(0, 8)
    for (const r of cr) out.push({ id: `c:${r.sym}`, label: `${r.sym} · ${r.name}`, hint: 'crypto', act: close(() => (location.hash = assetHref(r.sym, 'crypto'))) })
    const uni = (universe || []).filter((u: any) => s && (u.sym.toUpperCase().startsWith(S) || u.label.toUpperCase().includes(S))).slice(0, 8)
    for (const u of uni) out.push({ id: `u:${u.sym}`, label: `${u.label} · ${u.sym}`, hint: u.kind === 'equity' ? 'stock' : u.kind, act: close(() => (location.hash = assetHref(u.sym, 'stock'))) })
    const st: string[] = (settings?.watchlist?.stocks || []).filter((x: string) => !uni.some((u: any) => u.sym === x))
    for (const x of st.filter((x) => s && x.includes(S)).slice(0, 5))
      out.push({ id: `s:${x}`, label: x, hint: 'stock', act: close(() => (location.hash = assetHref(x, 'stock'))) })
    if (s && /^[A-Z0-9.^=-]{1,12}$/.test(S)) {
      if (!cr.find((r: any) => r.sym === S)) out.push({ id: `cx:${S}`, label: `Open ${S} as crypto`, hint: 'crypto', act: close(() => (location.hash = assetHref(S, 'crypto'))) })
      if (!st.includes(S)) out.push({ id: `sx:${S}`, label: `Open ${S} as stock / index`, hint: 'stock', act: close(() => (location.hash = assetHref(S, 'stock'))) })
    }
    if (s) out.push({ id: 'ask', label: `Ask Rook: “${s}”`, hint: '↵ AI', act: close(() => go(`ask?q=${encodeURIComponent(s)}`)) })
    return out.slice(0, 16)
  }, [q, markets, universe, settings, onClose])

  if (!open) return null
  return (
    <div className="overlay" onMouseDown={onClose}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <div className="row" style={{ paddingLeft: '0.875rem' }}>
          <Search size={16} className="muted" />
          <input
            ref={inp}
            value={q}
            placeholder="Search assets, pages, or ask anything…"
            onChange={(e) => {
              setQ(e.target.value)
              setSel(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onClose()
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setSel((s) => Math.min(s + 1, items.length - 1))
              }
              if (e.key === 'ArrowUp') {
                e.preventDefault()
                setSel((s) => Math.max(s - 1, 0))
              }
              if (e.key === 'Enter') items[sel]?.act()
            }}
          />
        </div>
        <div className="items">
          {items.map((it, i) => (
            <div key={it.id} className={`it ${i === sel ? 'sel' : ''}`} onMouseEnter={() => setSel(i)} onClick={it.act}>
              <span>{it.label}</span>
              <span className="k">{it.hint}</span>
            </div>
          ))}
          {!items.length && <div className="empty">Nothing matches</div>}
        </div>
      </div>
    </div>
  )
}
