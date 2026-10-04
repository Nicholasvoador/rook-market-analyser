import { useEffect, useState } from 'react'
import { BellRing, Plus, Sparkles, Trash2, X } from 'lucide-react'
import Markdown from '../components/Markdown'
import { Err, Panel, Seg, Toggle } from '../components/ui'
import { del, post, put, useApi } from '../lib/api'
import { ago } from '../lib/fmt'
import { hub } from '../lib/hub'
import { useRoute } from '../lib/store'

const METRICS: [string, string][] = [
  ['price', 'Price'],
  ['chg24', '24h change %'],
  ['rsi_1h', 'RSI 1h'],
  ['rsi_1d', 'RSI 1d'],
  ['dist_ema200_1d', '% from daily EMA200'],
  ['ema9_21_1h', 'EMA9 − EMA21 (1h)'],
  ['funding', 'Funding %/8h'],
  ['oi_chg24', 'OI 24h change %'],
  ['sentiment', 'News/social tone'],
  ['mentions', 'Mentions 24h'],
  ['velocity', 'Mention velocity x'],
  ['score', 'Rook score'],
  ['p_up_24', 'Model P(up) 24h'],
  ['fng', 'Crypto Fear & Greed'],
  ['liq_1h', 'Liquidations 1h (USD)'],
]
const OPS: [string, string][] = [
  ['>', '>'],
  ['<', '<'],
  ['crosses_above', 'crosses ↑'],
  ['crosses_below', 'crosses ↓'],
]

type Cond = { sym: string; kind: string; metric: string; op: string; value: number }
type Spec = { name: string; logic: 'all' | 'any'; cooldown_min: number; ai_analysis?: boolean; conditions: Cond[] }

const blank = (sym = 'BTC', kind = 'crypto'): Spec => ({
  name: '',
  logic: 'all',
  cooldown_min: 60,
  ai_analysis: true,
  conditions: [{ sym, kind, metric: 'price', op: 'crosses_above', value: 0 }],
})

export default function Alerts() {
  const route = useRoute()
  const { data, reload, setData } = useApi<any>('/api/alerts', 30000)
  const [nl, setNl] = useState('')
  const [spec, setSpec] = useState<Spec>(() => blank(route.query.get('sym') || 'BTC', route.query.get('kind') || 'crypto'))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [perm, setPerm] = useState(typeof Notification !== 'undefined' ? Notification.permission : 'denied')

  useEffect(() => {
    const a = hub.on('alert', () => reload())
    const b = hub.on('alert_ai', (ev) =>
      setData((d: any) => d && { ...d, events: d.events.map((e: any) => (e.id === ev.id ? { ...e, data: { ...e.data, ai: { text: ev.text, model: ev.model } } } : e)) }),
    )
    return () => {
      a()
      b()
    }
  }, [reload, setData])

  const parse = async () => {
    if (!nl.trim()) return
    setBusy(true)
    setErr(null)
    try {
      const s = await post('/api/alerts/parse', { text: nl })
      setSpec({ ...blank(), ...s, ai_analysis: true, name: s.name || nl.slice(0, 60) })
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    const s = { ...spec, name: spec.name || spec.conditions.map((c) => `${c.sym} ${c.metric} ${c.op} ${c.value}`).join(' & ') }
    await post('/api/alerts', s)
    setSpec(blank())
    setNl('')
    reload()
  }

  const setC = (i: number, patch: Partial<Cond>) => setSpec((s) => ({ ...s, conditions: s.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) }))

  return (
    <div className="col">
      <div className="page-head">
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Alerts</h1>
          <div className="muted small">Condition engine checked every 60s. Combine price, TA, funding, sentiment and model probability; optionally have the AI explain each trigger.</div>
        </div>
        <span className="grow" />
        {perm !== 'granted' && typeof Notification !== 'undefined' && (
          <button className="btn" onClick={async () => setPerm(await Notification.requestPermission())}>
            <BellRing /> Enable browser notifications
          </button>
        )}
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.3fr) minmax(0,1fr)' }}>
        <Panel title="Create alert">
          <div className="field">
            <span>Describe it in plain language: the AI builds the conditions</span>
            <div className="row">
              <input className="input grow" aria-label="Describe an alert in plain words" value={nl} onChange={(e) => setNl(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && parse()} placeholder="e.g. tell me when SOL RSI 1h drops below 30 while funding is negative" />
              <button className="btn" onClick={parse} disabled={busy}><Sparkles /> {busy ? 'parsing…' : 'Build'}</button>
            </div>
          </div>
          <Err e={err} />
          <hr className="sep" />
          <div className="col" style={{ gap: '0.5rem' }}>
            {spec.conditions.map((c, i) => (
              <div key={i} className="row wrap">
                {i > 0 && <span className="chip">{spec.logic === 'all' ? 'AND' : 'OR'}</span>}
                <input className="input" aria-label="Symbol" style={{ width: '6.25rem' }} value={c.sym} onChange={(e) => setC(i, { sym: e.target.value.toUpperCase() })} placeholder="SYM" />
                <select className="input" aria-label="Asset type" value={c.kind} onChange={(e) => setC(i, { kind: e.target.value })}>
                  <option value="crypto">crypto</option>
                  <option value="stock">stock</option>
                  <option value="market">market</option>
                </select>
                <select className="input" aria-label="Metric" value={c.metric} onChange={(e) => setC(i, { metric: e.target.value })}>
                  {METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <select className="input" aria-label="Comparison" value={c.op} onChange={(e) => setC(i, { op: e.target.value })}>
                  {OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <input className="input num" aria-label="Threshold" style={{ width: '6.875rem' }} type="number" value={c.value} onChange={(e) => setC(i, { value: +e.target.value })} />
                {spec.conditions.length > 1 && (
                  <button className="btn ghost sm" onClick={() => setSpec((s) => ({ ...s, conditions: s.conditions.filter((_, j) => j !== i) }))}><X size={12} /></button>
                )}
              </div>
            ))}
            <div className="row wrap">
              <button className="btn sm" onClick={() => setSpec((s) => ({ ...s, conditions: [...s.conditions, { ...s.conditions[0], metric: 'rsi_1h', op: '<', value: 30 }] }))}><Plus /> condition</button>
              <Seg value={spec.logic} options={[['all', 'ALL match'], ['any', 'ANY match']]} onChange={(v) => setSpec({ ...spec, logic: v })} />
              <span className="small muted">cooldown</span>
              <input className="input num" aria-label="Cooldown in minutes" style={{ width: '4.375rem' }} type="number" value={spec.cooldown_min} onChange={(e) => setSpec({ ...spec, cooldown_min: +e.target.value })} />
              <span className="small muted">min</span>
              <span className="row small"><Toggle label="AI explains trigger" on={!!spec.ai_analysis} onChange={(v) => setSpec({ ...spec, ai_analysis: v })} /> AI explains trigger</span>
            </div>
            <div className="row">
              <input className="input grow" aria-label="Alert name" placeholder="Alert name (optional)" value={spec.name} onChange={(e) => setSpec({ ...spec, name: e.target.value })} />
              <button className="btn primary" onClick={save}>Save alert</button>
            </div>
          </div>
        </Panel>

        <Panel title="Active alerts" flush>
          {(data?.alerts || []).map((a: any) => (
            <div key={a.id} className="newsi" style={{ alignItems: 'center' }}>
              <Toggle label={`Alert ${a.name || a.id} enabled`} on={!!a.enabled} onChange={async (v) => { await put(`/api/alerts/${a.id}`, { enabled: v }); reload() }} />
              <div className="grow">
                <div>{a.name}</div>
                <div className="tiny muted">
                  {a.spec.conditions.map((c: Cond) => `${c.sym} ${c.metric} ${c.op} ${c.value}`).join(a.spec.logic === 'all' ? ' AND ' : ' OR ')}
                  {' · '}cooldown {a.cooldown_min}m{a.last_fired ? ` · fired ${ago(a.last_fired)} ago` : ''}
                </div>
              </div>
              <button className="btn ghost sm danger" onClick={async () => { await del(`/api/alerts/${a.id}`); reload() }}><Trash2 size={12} /></button>
            </div>
          ))}
          {data && !data.alerts.length && <div className="empty">No alerts yet</div>}
        </Panel>
      </div>

      <Panel title="Triggered" sub="history" flush>
        {(data?.events || []).map((e: any) => (
          <div key={e.id} className="newsi" style={{ display: 'block' }}>
            <div className="row">
              <BellRing size={13} className="accent" />
              <b style={{ fontWeight: 500 }}>{e.name}</b>
              <span className="small muted">{e.message}</span>
              <span className="grow" />
              <span className="tiny faint">{ago(e.ts)} ago</span>
            </div>
            {e.data?.ai && (
              <div style={{ marginTop: '0.375rem', paddingLeft: '1.3125rem' }}>
                <span className="chip accent" style={{ marginBottom: '0.25rem' }}>{e.data.ai.model}</span>
                <Markdown text={e.data.ai.text} />
              </div>
            )}
          </div>
        ))}
        {data && !data.events.length && <div className="empty">Nothing has fired yet</div>}
      </Panel>
    </div>
  )
}
