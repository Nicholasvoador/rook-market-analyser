import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { isNum, pct, price, tone } from '../lib/fmt'
import { GLOSSARY } from '../lib/glossary'

export function Panel(p: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children?: ReactNode; className?: string; flush?: boolean; style?: React.CSSProperties; bodyStyle?: React.CSSProperties }) {
  return (
    <section className={`panel ${p.className || ''}`} style={p.style}>
      {(p.title || p.right) && (
        <div className="panel-h">
          {p.title && <span className="t">{p.title}</span>}
          {p.sub && <span className="s">{p.sub}</span>}
          {p.right && <div className="r">{p.right}</div>}
        </div>
      )}
      <div className={`panel-b ${p.flush ? 'flush' : ''}`} style={p.bodyStyle}>
        {p.children}
      </div>
    </section>
  )
}

/** Signed percentage with a ▲/▼ glyph (direction is never conveyed by colour alone). */
export function Delta({ v, d = 2, className = '' }: { v: unknown; d?: number; className?: string }) {
  return <span className={`num dir ${tone(v)} ${className}`}>{pct(v, d)}</span>
}

export function Skel({ h = 14, w = '100%', n = 1 }: { h?: number; w?: number | string; n?: number }) {
  return (
    <div className="col" style={{ gap: '0.5rem' }} aria-busy="true" aria-label="Loading">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="skel" style={{ height: `${h / 16}rem`, width: typeof w === 'number' ? `${w / 16}rem` : w }} />
      ))}
    </div>
  )
}

/** Jargon with a plain-language tooltip on hover AND keyboard focus. */
export function Term({ k, children }: { k: string; children?: ReactNode }) {
  const id = useId()
  const g = GLOSSARY[k]
  if (!g) return <>{children}</>
  const place = (e: React.SyntheticEvent<HTMLSpanElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const w = Math.min(window.innerWidth - 16, parseFloat(getComputedStyle(document.documentElement).fontSize) * 20)
    const below = r.bottom + 160 < window.innerHeight
    e.currentTarget.style.setProperty('--tx', `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`)
    e.currentTarget.style.setProperty('--ty', below ? `${r.bottom + 6}px` : 'auto')
    e.currentTarget.style.setProperty('--tb', below ? 'auto' : `${window.innerHeight - r.top + 6}px`)
  }
  return (
    <span className="term" tabIndex={0} aria-describedby={id} onMouseEnter={place} onFocus={place}>
      {children ?? g[0]}
      <span role="tooltip" id={id} className="tip">
        <b>{g[0]}</b>
        <br />
        {g[1]}
      </span>
    </span>
  )
}

const TONE_WORD: Record<string, string> = { bull: 'positive', bear: 'negative', neutral: 'neutral' }

/** Deterministic plain-language summary of an asset (from /api/signals/one -> readout). */
export function Readout({ ro, score }: { ro: any; score?: number }) {
  if (!ro) return <Skel n={6} />
  const cls = isNum(score) ? (score >= 18 ? 'bull' : score <= -18 ? 'bear' : '') : ''
  return (
    <div className="readout">
      <div className={`ro-bottom ${cls}`}>
        <span className="ro-label">Bottom line</span>
        {ro.bottom_line}
      </div>
      {(ro.lines || []).map((l: any) => (
        <div className="ro-line" key={l.k}>
          <span className="ro-k">
            <i className={l.tone} aria-hidden="true" />
            {l.k}
            <span className="sr-only">({TONE_WORD[l.tone] || l.tone})</span>
          </span>
          <span className="ro-t">{l.text}</span>
        </div>
      ))}
    </div>
  )
}

export function Verdict({ v }: { v?: string }) {
  if (!v) return null
  const cls = v === 'historically favorable' || v === 'big moves more likely' ? 'good' : v === 'historically unfavorable' ? 'bad' : ''
  return <span className={`verdict ${cls}`}>{v}</span>
}

export function SetupList({ items, empty = 'No setups firing right now.' }: { items?: any[]; empty?: string }) {
  if (!items) return <Skel n={3} />
  if (!items.length) return <div className="muted small">{empty}</div>
  return (
    <div>
      {items.map((a) => {
        const st = a.stats || {}
        return (
          <div className="setup" key={`${a.id}-${a.tf}`}>
            <div className="row">
              <span className={`chip ${a.dir === 'bull' ? 'up' : a.dir === 'bear' ? 'down' : ''}`}>{a.dir}</span>
              <b>{a.label}</b>
              <span className="chip">{a.tf}</span>
              <span className="grow" />
              <Verdict v={st.verdict} />
            </div>
            <div className="meta">{a.explain}</div>
            {st.n > 0 && (
              <div className="meta">
                Historically: {(st.hit * 100).toFixed(0)}% {a.dir === 'neutral' ? 'bigger-than-usual moves' : a.dir === 'bull' ? 'higher' : 'lower'} after {st.horizon}{' '}
                vs {(st.base * 100).toFixed(0)}% <Term k="base">base rate</Term> · n={st.n} · avg {pct(st.avg * 100, 2)}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** Support/resistance ladder around the current price. */
export function Levels({ lv, last }: { lv?: any; last?: number }) {
  if (!lv) return <Skel n={5} />
  const res = [...(lv.resistance || [])].reverse()
  const sup = lv.support || []
  const row = (e: any, kind: string) => (
    <div className="lv" key={`${kind}${e.price}`}>
      <span className={kind === 'r' ? 'down' : 'up'}>{kind === 'r' ? 'Resistance' : 'Support'}</span>
      <span className="num">{price(e.price)}</span>
      <span className={`num ${tone(e.dist_pct)}`}>{pct(e.dist_pct, 1)}</span>
      <span className="tiny muted">{e.touches}×</span>
    </div>
  )
  if (!res.length && !sup.length) return <div className="muted small">Not enough swing history yet.</div>
  return (
    <div className="ladder">
      {res.map((e: any) => row(e, 'r'))}
      <div className="lv now">
        <span>Now</span>
        <span className="num">{price(last)}</span>
        <span />
        <span />
      </div>
      {sup.map((e: any) => row(e, 's'))}
    </div>
  )
}

export function Err({ e }: { e?: string | null }) {
  return e ? <div className="err">{e}</div> : null
}

/** Flashes green/red on change. */
export function Flash({ v, children, className = '' }: { v: number | undefined; children: ReactNode; className?: string }) {
  const prev = useRef(v)
  const [cls, setCls] = useState('')
  useEffect(() => {
    if (isNum(v) && isNum(prev.current) && v !== prev.current) {
      setCls(v > prev.current ? 'flash-up' : 'flash-down')
      const t = setTimeout(() => setCls(''), 600)
      prev.current = v
      return () => clearTimeout(t)
    }
    prev.current = v
  }, [v])
  return <span className={`${className} ${cls}`}>{children}</span>
}

export function Spark({ data, w = 90, h = 26, color }: { data: number[]; w?: number; h?: number; color?: string }) {
  if (!data || data.length < 2) return <svg width={w} height={h} />
  const mn = Math.min(...data)
  const mx = Math.max(...data)
  const r = mx - mn || 1
  const pts = data.map((v, i) => `${((i / (data.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - mn) / r) * (h - 4)).toFixed(1)}`)
  const c = color || (data[data.length - 1] >= data[0] ? 'var(--up)' : 'var(--down)')
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <polyline points={pts.join(' ')} fill="none" stroke={c} strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  )
}

/** Semicircle gauge 0..100 (fear & greed). */
export function Gauge({ v, label, size = 92 }: { v: number | undefined; label?: string; size?: number }) {
  const val = isNum(v) ? Math.max(0, Math.min(100, v)) : 0
  const r = size / 2 - 6
  const cx = size / 2
  const cy = size / 2
  const ang = Math.PI * (1 - val / 100)
  const x = cx + r * Math.cos(ang)
  const y = cy - r * Math.sin(ang)
  const col = val < 25 ? 'var(--down)' : val < 45 ? '#f0896b' : val < 56 ? 'var(--muted)' : val < 75 ? '#7fcf9f' : 'var(--up)'
  return (
    <svg width={size} height={size / 2 + 8} viewBox={`0 0 ${size} ${size / 2 + 8}`}>
      <path d={`M 6 ${cy} A ${r} ${r} 0 0 1 ${size - 6} ${cy}`} fill="none" stroke="var(--panel-3)" strokeWidth="6" strokeLinecap="round" />
      {isNum(v) && (
        <path d={`M 6 ${cy} A ${r} ${r} 0 0 1 ${x.toFixed(2)} ${y.toFixed(2)}`} fill="none" stroke={col} strokeWidth="6" strokeLinecap="round" />
      )}
      <text x={cx} y={cy - 6} textAnchor="middle" fill="var(--ink)" fontFamily="var(--mono)" fontSize="17" fontWeight="500">
        {isNum(v) ? Math.round(val) : '—'}
      </text>
      {label && (
        <text x={cx} y={cy + 7} textAnchor="middle" fill={col} fontSize="9.5" letterSpacing="0.04em">
          {label.toUpperCase()}
        </text>
      )}
    </svg>
  )
}

/** Centered bar for -1..1 values. */
export function CBar({ v, max = 1 }: { v: number | null | undefined; max?: number }) {
  const x = isNum(v) ? Math.max(-1, Math.min(1, v / max)) : 0
  const w = Math.abs(x) * 50
  return (
    <div className="cbar">
      <i style={{ left: x >= 0 ? '50%' : `${50 - w}%`, width: `${w}%`, background: x >= 0 ? 'var(--up)' : 'var(--down)' }} />
    </div>
  )
}

export function Seg<T extends string | number>({ value, options, onChange, label }: { value: T; options: (T | [T, string])[]; onChange: (v: T) => void; label?: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => {
        const v = (Array.isArray(o) ? o[0] : o) as T
        const l = Array.isArray(o) ? o[1] : String(o)
        return (
          <button key={String(v)} className={v === value ? 'on' : ''} aria-pressed={v === value} onClick={() => onChange(v)}>
            {l}
          </button>
        )
      })}
    </div>
  )
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return <button className={`toggle ${on ? 'on' : ''}`} onClick={() => onChange(!on)} role="switch" aria-checked={on} aria-label={label} />
}

export function SymIcon({ sym, image }: { sym: string; image?: string | null }) {
  return image ? <img src={image} alt="" loading="lazy" /> : <span className="tk">{sym.slice(0, 2)}</span>
}

export function EdgeBadge({ edge }: { edge: string }) {
  const cls = edge.startsWith('strong') || edge === 'moderate' ? 'up' : edge.startsWith('weak') ? 'ice' : edge === 'no edge' ? '' : 'accent'
  return <Term k="edge"><span className={`chip ${cls}`}>{edge}</span></Term>
}

export function ScoreChip({ score, label }: { score: number; label?: string }) {
  const cls = score >= 18 ? 'up' : score <= -18 ? 'down' : ''
  return (
    <span className={`chip ${cls} num`} title={label}>
      {score > 0 ? '+' : ''}
      {Math.round(score)}
    </span>
  )
}

export function SentChip({ v, n }: { v?: number | null; n?: number }) {
  if (!isNum(v) || !n) return <span className="faint">—</span>
  const cls = v >= 0.12 ? 'up' : v <= -0.12 ? 'down' : ''
  return (
    <span className={`chip ${cls}`} title={`${n} mentions`}>
      {v >= 0.12 ? 'bull' : v <= -0.12 ? 'bear' : 'neutral'} <span className="num">{v > 0 ? '+' : ''}{v.toFixed(2)}</span>
    </span>
  )
}
