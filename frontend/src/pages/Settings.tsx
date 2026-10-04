import { useEffect, useState, useSyncExternalStore } from 'react'
import { CheckCircle2, KeyRound, Play, RefreshCw, X, XCircle } from 'lucide-react'
import { Err, Panel, Seg, Skel, Term, Toggle } from '../components/ui'
import { del, post, put, useApi } from '../lib/api'
import { ago, num, until } from '../lib/fmt'
import { onVenueLat, probeVenues, venueLat } from '../lib/live'
import { applyScale, loadSettings, saveSettings, useSettings } from '../lib/store'

const EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
const SECTIONS: [string, string][] = [
  ['profile', 'You'],
  ['display', 'Display & accessibility'],
  ['ai', 'AI models'],
  ['elfa', 'Elfa (X/Telegram)'],
  ['latency', 'Latency'],
  ['sources', 'Data sources'],
  ['keys', 'API keys'],
  ['watch', 'Watchlists'],
  ['predict', 'Predictions'],
  ['risk', 'Risk profile'],
  ['about', 'About'],
]

function ModelPicker({ label, spec, onChange, opts, allowDefault }: { label: string; spec: any; onChange: (s: any) => void; opts: any; allowDefault?: boolean }) {
  const provs: any[] = opts?.providers || []
  const cur = provs.find((p) => p.provider === spec.provider)
  const isElfa = spec.provider === 'elfa'
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(12rem, 1fr))', alignItems: 'end', gap: '0.625rem' }}>
      <div className="h2" style={{ paddingBottom: '0.375rem' }}>{label}</div>
      <label className="field">
        <span>Provider</span>
        <select className="input" value={spec.provider || ''} onChange={(e) => onChange({ ...spec, provider: e.target.value, model: '' })}>
          {allowDefault && <option value="">Hermes default ({opts?.default?.provider || '…'})</option>}
          {provs.map((p) => (
            <option key={p.provider} value={p.provider}>
              {p.name || p.provider}
              {p.available === false ? ' (plan required)' : ''}
            </option>
          ))}
          {spec.provider && !cur && <option value={spec.provider}>{spec.provider}</option>}
        </select>
      </label>
      <label className="field">
        <span>Model</span>
        {spec.provider ? (
          <select className="input" value={spec.model || ''} onChange={(e) => onChange({ ...spec, model: e.target.value })}>
            <option value="">choose…</option>
            {(cur?.models || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
            {spec.model && !(cur?.models || []).includes(spec.model) && <option value={spec.model}>{spec.model}</option>}
          </select>
        ) : (
          <input className="input" disabled value={opts?.default?.model || 'Hermes default'} />
        )}
      </label>
      <label className="field">
        <span>Reasoning effort</span>
        <select className="input" value={spec.reasoning_effort || ''} disabled={isElfa} onChange={(e) => onChange({ ...spec, reasoning_effort: e.target.value })}>
          {EFFORTS.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
      </label>
      {cur?.note && <div className="tiny muted" style={{ gridColumn: '1 / -1' }}>{cur.note}</div>}
    </div>
  )
}

function AISection({ s }: { s: any }) {
  const { data: opts, error } = useApi<any>('/api/ai/models')
  const { data: st } = useApi<any>('/api/status', 15000)
  const [ai, setAi] = useState(s.ai)
  const [test, setTest] = useState<Record<string, any>>({})
  const [saved, setSaved] = useState(false)
  useEffect(() => setAi(s.ai), [s.ai])
  const save = async () => {
    await saveSettings({ ai })
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }
  const run = async (target: string) => {
    setTest((t) => ({ ...t, [target]: { busy: true } }))
    await saveSettings({ ai })
    const r = await post('/api/ai/test', { target })
    setTest((t) => ({ ...t, [target]: r }))
  }
  const testRes = (k: string) => {
    const r = test[k]
    if (!r) return null
    if (r.busy) return <span className="small muted" role="status">testing…</span>
    return r.ok ? (
      <span className="small up row" role="status"><CheckCircle2 size={13} aria-hidden="true" /> {r.model} answered in {r.ms}ms</span>
    ) : (
      <span className="small down row" role="status"><XCircle size={13} aria-hidden="true" /> {r.error || r.reply || 'failed'}</span>
    )
  }
  return (
    <Panel title="AI models" sub="via Hermes API server (+ Elfa's own model)" right={<span className="row small"><span className={`dot ${st?.hermes?.ok ? 'ok' : 'bad'}`} /> {st?.hermes?.url} {st?.hermes?.key_set ? '· key set' : '· no key'}</span>}>
      <Err e={error} />
      <div className="col" style={{ gap: '0.875rem' }}>
        <ModelPicker label="Primary" spec={ai.primary} onChange={(p) => setAi({ ...ai, primary: p })} opts={opts} allowDefault />
        <div className="row">
          <button className="btn sm" onClick={() => run('primary')}><Play size={12} aria-hidden="true" /> Test primary</button>
          {testRes('primary')}
        </div>
        <ModelPicker label="Fallback" spec={ai.fallback} onChange={(p) => setAi({ ...ai, fallback: p })} opts={opts} />
        <div className="row">
          <button className="btn sm" onClick={() => run('fallback')}><Play size={12} aria-hidden="true" /> Test fallback</button>
          {testRes('fallback')}
        </div>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(13rem, 1fr))', gap: '0.75rem' }}>
          <label className="field"><span>Answer style</span>
            <select className="input" value={ai.style || 'standard'} onChange={(e) => setAi({ ...ai, style: e.target.value })}>
              <option value="standard">Standard (trader vocabulary)</option>
              <option value="plain">Plain language (explains jargon)</option>
            </select>
          </label>
          <label className="field"><span>Portfolio detail shared with the AI</span>
            <select className="input" value={ai.portfolio_detail || 'weights'} onChange={(e) => setAi({ ...ai, portfolio_detail: e.target.value })}>
              <option value="weights">Weights & % only (private)</option>
              <option value="full">Include USD amounts</option>
            </select>
          </label>
          <label className="field"><span>Expert-mode effort</span>
            <select className="input" value={ai.expert_effort} onChange={(e) => setAi({ ...ai, expert_effort: e.target.value })}>{EFFORTS.map((x) => <option key={x}>{x}</option>)}</select>
          </label>
          <label className="field"><span>First-output timeout (s)</span><input className="input num" type="number" value={ai.first_token_timeout_s} onChange={(e) => setAi({ ...ai, first_token_timeout_s: +e.target.value })} /></label>
          <label className="field"><span>Total timeout (s)</span><input className="input num" type="number" value={ai.timeout_s} onChange={(e) => setAi({ ...ai, timeout_s: +e.target.value })} /></label>
          <label className="field"><span>Auto market brief every (h, 0 = off)</span><input className="input num" type="number" value={ai.auto_brief_hours} onChange={(e) => setAi({ ...ai, auto_brief_hours: +e.target.value })} /></label>
          <div className="field"><span>Last resort: hermes CLI</span><div style={{ paddingTop: '0.375rem' }}><Toggle label="Use the hermes CLI as last resort" on={ai.cli_fallback} onChange={(v) => setAi({ ...ai, cli_fallback: v })} /></div></div>
        </div>
        <div className="row wrap">
          <button className="btn primary" onClick={save}>Save AI settings</button>
          {saved && <span className="small up" role="status">saved</span>}
          <span className="tiny faint">Chain: primary → fallback (also continues answers that were cut off) → `hermes chat -Q -t web` → error. Wallet addresses are never sent to any model.</span>
        </div>
      </div>
    </Panel>
  )
}

function Profile({ s }: { s: any }) {
  const p = s.profile || {}
  const [name, setName] = useState(p.name || '')
  const [tz, setTz] = useState(p.timezone || '')
  useEffect(() => {
    setName(p.name || '')
    setTz(p.timezone || '')
  }, [p.name, p.timezone])
  const sysTz = Intl.DateTimeFormat().resolvedOptions().timeZone
  return (
    <Panel title="You" sub="stored only in ~/.config/rookery/settings.json">
      <div className="row wrap" style={{ gap: '1rem', alignItems: 'end' }}>
        <label className="field"><span>Name (how the AI addresses you)</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} onBlur={() => saveSettings({ profile: { name: name.trim() } })} placeholder="optional" style={{ width: '14rem' }} />
        </label>
        <label className="field"><span>Timezone (IANA)</span>
          <input className="input mono" value={tz} onChange={(e) => setTz(e.target.value)} onBlur={() => saveSettings({ profile: { timezone: tz.trim() } })} placeholder={sysTz} style={{ width: '14rem' }} />
        </label>
        <div className="field"><span>Home currency</span>
          <Seg label="Home currency" value={p.home_currency || 'USD'} options={['USD', 'BRL']} onChange={(v) => saveSettings({ profile: { home_currency: v }, display: { show_brl: v === 'BRL' } })} />
        </div>
      </div>
    </Panel>
  )
}

function Display({ s }: { s: any }) {
  const d = s.display
  const [scale, setScale] = useState<number>(d.ui_scale ?? 1.15)
  useEffect(() => setScale(d.ui_scale ?? 1.15), [d.ui_scale])
  return (
    <Panel title="Display & accessibility" sub="everything scales with one setting (also Ctrl + / Ctrl − or A−/A+ in the top bar)">
      <div className="col" style={{ gap: '1rem' }}>
        <div className="row wrap" style={{ gap: '0.875rem' }}>
          <label htmlFor="ui-scale" className="h2">Interface size</label>
          <input id="ui-scale" type="range" min={0.9} max={1.6} step={0.05} value={scale} style={{ width: '18rem' }}
            onChange={(e) => { const v = +e.target.value; setScale(v); applyScale(v) }}
            onMouseUp={() => saveSettings({ display: { ui_scale: scale } })} onKeyUp={() => saveSettings({ display: { ui_scale: scale } })}
            onTouchEnd={() => saveSettings({ display: { ui_scale: scale } })} aria-valuetext={`${Math.round(scale * 100)} percent`} />
          <b className="num">{Math.round(scale * 100)}%</b>
          {[1, 1.15, 1.3, 1.45].map((v) => (
            <button key={v} className={`btn sm ${Math.abs(scale - v) < 0.01 ? 'primary' : ''}`} onClick={() => { setScale(v); applyScale(v); saveSettings({ display: { ui_scale: v } }) }}>
              {v === 1 ? 'Compact' : v === 1.15 ? 'Default' : v === 1.3 ? 'Large' : 'Extra large'}
            </button>
          ))}
        </div>
        <div className="row wrap" style={{ gap: '1.25rem' }}>
          <label className="row small">High contrast <Toggle label="High contrast" on={!!d.high_contrast} onChange={(v) => saveSettings({ display: { high_contrast: v } })} /></label>
          <label className="row small">Reduce motion (stops the ticker tape) <Toggle label="Reduce motion" on={!!d.reduce_motion} onChange={(v) => saveSettings({ display: { reduce_motion: v } })} /></label>
          <span className="row small">Density <Seg label="Density" value={d.density} options={[['comfortable', 'Comfortable'], ['compact', 'Compact']]} onChange={(v) => saveSettings({ display: { density: v } })} /></span>
          <span className="row small">Currency <Seg label="Display currency" value={d.currency} options={['USD', 'BRL']} onChange={(v) => saveSettings({ display: { currency: v } })} /></span>
          <label className="row small">Accent <input type="color" value={d.accent} onChange={(e) => saveSettings({ display: { accent: e.target.value } })} style={{ width: '2.125rem', height: '1.5rem', border: 0, background: 'none' }} aria-label="Accent colour" /></label>
          <label className="row small">Desktop notifications <Toggle label="Desktop notifications" on={s.alerts.desktop_notify} onChange={(v) => saveSettings({ alerts: { ...s.alerts, desktop_notify: v } })} /></label>
        </div>
        <div className="tiny faint">Direction is always shown with ▲/▼ as well as colour. Dotted-underlined terms explain themselves on hover or keyboard focus (Tab).</div>
      </div>
    </Panel>
  )
}

function ElfaSection({ s }: { s: any }) {
  const { data, error, reload } = useApi<any>('/api/elfa/status', 60000)
  const cfg = s.elfa
  const b = data?.budget
  const usedPct = b ? Math.min(100, (b.used / b.limit) * 100) : 0
  return (
    <Panel title="Elfa (X/Telegram social data)" sub={data ? `tier: ${data.tier || '?'} · ${data.configured ? 'key set' : 'no key'}` : ''}
      right={<Toggle label="Enable Elfa" on={cfg.enabled} onChange={(v) => saveSettings({ elfa: { enabled: v } })} />}>
      <Err e={error} />
      {!data && <Skel n={4} />}
      {data && (
        <div className="col" style={{ gap: '0.875rem' }}>
          <div>
            <div className="row small" style={{ marginBottom: '0.375rem' }}>
              <b>Credits this month</b>
              <span className="num">{num(b.used, 0)} / {num(b.limit, 0)}</span>
              <span className="muted">· today {num(b.today, 0)} of {num(b.daily_allowance, 0)} auto-allowance · reserve {b.reserve} for on-demand · {b.days_left} days left</span>
              <span className="grow" />
              <span className="tiny faint">source: {b.source === 'elfa' ? 'Elfa key-status' : 'local count'}</span>
            </div>
            <div className="budget" role="progressbar" aria-valuemin={0} aria-valuemax={b.limit} aria-valuenow={b.used} aria-label="Elfa credits used"><i style={{ width: `${usedPct}%` }} /></div>
          </div>
          <table className="t">
            <thead><tr><th>Background job</th><th className="num">Cost</th><th className="num">Every (h)</th><th>Last run</th><th>Next</th><th>Status</th></tr></thead>
            <tbody>
              {Object.entries(data.jobs || {}).map(([k, j]: [string, any]) => (
                <tr key={k}>
                  <td>{({ trending: 'Trending tokens (X mentions)', cas: 'Trending contracts (X + Telegram)', news: 'Token news posts', narratives: 'Trending narratives' } as any)[k] || k}</td>
                  <td className="num">{j.cost}</td>
                  <td className="num">
                    <input className="input num" type="number" min={0} style={{ width: '4rem', height: '1.625rem' }} defaultValue={j.cadence_h} aria-label={`${k} cadence in hours`}
                      onBlur={(e) => saveSettings({ elfa: { cadence_h: { ...cfg.cadence_h, [k]: +e.target.value } } }).then(reload)} />
                  </td>
                  <td className="muted small">{j.last ? `${ago(j.last)} ago` : 'never'}</td>
                  <td className="muted small">{j.next ? until(j.next) : '—'}</td>
                  <td className={`small ${j.error ? 'warn' : 'up'}`}>{j.error || 'ok'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row wrap small" style={{ gap: '1.25rem' }}>
            <label className="row">Monthly credits <input className="input num" type="number" style={{ width: '5.5rem', height: '1.625rem' }} defaultValue={cfg.monthly_credits} onBlur={(e) => saveSettings({ elfa: { monthly_credits: +e.target.value } })} /></label>
            <label className="row">Reserve for on-demand <input className="input num" type="number" style={{ width: '4rem', height: '1.625rem' }} defaultValue={cfg.reserve_pct} onBlur={(e) => saveSettings({ elfa: { reserve_pct: +e.target.value } })} />%</label>
            <span className={data.chat?.available ? 'up' : 'muted'}>Ask Elfa (chat model): {data.chat?.available ? 'available' : data.chat?.reason || 'unavailable'}</span>
          </div>
          <div className="tiny faint">
            Background jobs pause automatically when the day's pace would overspend the month. Asset-page lookups (1 credit) and chatter summaries (5) can use the reserve. Results persist locally, so restarts never re-spend credits.
          </div>
        </div>
      )}
    </Panel>
  )
}

function useVenueLat() {
  return useSyncExternalStore(onVenueLat, () => venueLat)
}

function Latency({ s }: { s: any }) {
  const { data } = useApi<any>('/api/latency', 15000)
  const vl = useVenueLat()
  const [busy, setBusy] = useState(false)
  const probes: any[] = data?.probes || []
  const max = Math.max(1, ...probes.map((p) => p.p50_ms || 0))
  const vmax = Math.max(1, ...Object.values(vl.res || {}).map((v: any) => v.ms || 0))
  return (
    <Panel title={<Term k="latency">Latency</Term>} sub="sources are ranked by measured speed × reliability, fastest healthy first"
      right={<label className="row small">Adaptive routing <Toggle label="Adaptive latency routing" on={s.data.adaptive_latency !== false} onChange={(v) => saveSettings({ data: { adaptive_latency: v } })} /></label>}>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(24rem, 1fr))', gap: '1.25rem' }}>
        <div>
          <div className="row" style={{ marginBottom: '0.5rem' }}>
            <b className="small">Live feeds (your browser → exchange)</b>
            <span className="grow" />
            <button className="btn sm" disabled={busy} onClick={async () => { setBusy(true); await probeVenues(true); setBusy(false) }}>
              <RefreshCw size={12} aria-hidden="true" /> {busy ? 'Measuring 5s…' : 'Re-test'}
            </button>
          </div>
          <table className="t">
            <thead><tr><th>Venue</th><th className="num">Feed latency</th><th style={{ width: '40%' }} /><th className="num">Connect</th></tr></thead>
            <tbody>
              {Object.entries(vl.res || {}).sort((a: any, b: any) => (a[1].ms ?? 1e9) - (b[1].ms ?? 1e9)).map(([k, v]: [string, any]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="num">{v.ms != null ? `${v.ms}ms` : 'failed'}</td>
                  <td>{v.ms != null && <div className="lat-bar" style={{ width: `${(v.ms / vmax) * 100}%` }} />}</td>
                  <td className="num muted">{v.connect != null ? `${v.connect}ms` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="tiny faint" style={{ marginTop: '0.375rem' }}>
            {vl.t ? `Measured ${ago(vl.t / 1000)} ago (median of exchange event time → arrival). ` : 'Not measured yet. '}Charts and tickers stream from the fastest venue listing each pair.
          </div>
        </div>
        <div>
          <div className="row" style={{ marginBottom: '0.5rem' }}><b className="small">Backend REST probes (every 60s)</b><span className="grow" /><span className="tiny muted">spot venue order: {(data?.venues || []).join(' → ')}</span></div>
          <table className="t">
            <thead><tr><th>Source</th><th className="num">p50</th><th style={{ width: '35%' }} /><th className="num">p90</th><th className="num">fail</th></tr></thead>
            <tbody>
              {probes.map((p) => (
                <tr key={p.source}>
                  <td>{p.source}</td>
                  <td className="num">{p.p50_ms != null ? `${Math.round(p.p50_ms)}ms` : '—'}</td>
                  <td>{p.p50_ms != null && <div className="lat-bar" style={{ width: `${(p.p50_ms / max) * 100}%`, background: p.fail_pct > 10 ? 'var(--warn)' : undefined }} />}</td>
                  <td className="num muted">{p.p90_ms != null ? `${Math.round(p.p90_ms)}ms` : '—'}</td>
                  <td className={`num ${p.fail_pct > 0 ? 'warn' : 'muted'}`}>{p.fail_pct != null ? `${p.fail_pct}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Panel>
  )
}

function Sources() {
  const { data } = useApi<any[]>('/api/sources/health', 15000)
  return (
    <Panel title="Data sources" sub="every chain falls back automatically; failing sources are skipped briefly" flush>
      {!data && <div style={{ padding: '0.75rem' }}><Skel n={8} /></div>}
      <div className="scroll" style={{ maxHeight: '28.75rem' }}>
        <table className="t">
          <thead>
            <tr><th>Source</th><th>Last used for</th><th>Status</th><th className="num">Latency (avg)</th><th className="num">OK / fail</th><th className="num">Last OK</th><th>Last error</th></tr>
          </thead>
          <tbody>
            {(data || []).map((r) => (
              <tr key={r.source}>
                <td>{r.source}</td>
                <td className="muted small">{r.chain}</td>
                <td><span className="row small"><span className={`dot ${r.status === 'ok' ? 'ok' : r.status === 'down' ? 'bad' : ''}`} aria-hidden="true" />{r.status}</span></td>
                <td className="num">{r.ewma_ms ? `${r.ewma_ms}ms` : r.latency_ms ? `${r.latency_ms}ms` : '—'}</td>
                <td className="num">{r.ok} / <span className={r.fail ? 'down' : ''}>{r.fail}</span></td>
                <td className="num muted">{r.last_ok ? ago(r.last_ok) : '—'}</td>
                <td className="small muted" style={{ maxWidth: '23.75rem', overflow: 'hidden', textOverflow: 'ellipsis' }} title={r.last_error || ''}>{r.last_error || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function Keys() {
  const { secrets } = useSettings()
  const [vals, setVals] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const save = async () => {
    const changed = Object.fromEntries(Object.entries(vals).filter(([, v]) => v !== undefined))
    await put('/api/secrets', changed)
    setVals({})
    await loadSettings()
    setMsg('saved')
    setTimeout(() => setMsg(''), 1500)
  }
  return (
    <Panel title="API keys" sub="stored in ~/.config/rookery/secrets.env (mode 0600); the browser only ever sees set / not set">
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(24rem, 1fr))', gap: '0.75rem' }}>
        {Object.entries(secrets || {}).map(([k, v]) => (
          <label className="field" key={k}>
            <span className="row">
              <KeyRound size={12} aria-hidden="true" /> {v.label}
              <span className={`chip ${v.set ? 'up' : ''}`} style={{ marginLeft: 'auto' }}>{v.set ? 'set' : 'not set'}</span>
            </span>
            <div className="row">
              <input className="input grow mono" type="password" autoComplete="off" placeholder={v.set ? '•••••••• (leave empty to keep)' : k} value={vals[k] ?? ''} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
              {v.set && !k.startsWith('HERMES') && (
                <button className="btn ghost sm" aria-label={`Remove ${k}`} title="remove" onClick={async () => { await put('/api/secrets', { [k]: '' }); loadSettings() }}><X size={12} /></button>
              )}
            </div>
          </label>
        ))}
      </div>
      <div className="row wrap" style={{ marginTop: '0.75rem' }}>
        <button className="btn primary" onClick={save} disabled={!Object.values(vals).some(Boolean)}>Save keys</button>
        {msg && <span className="small up" role="status">{msg}</span>}
        <span className="tiny faint">Everything works keyless. Elfa adds X/Telegram data; a private Solana RPC (Helius, QuickNode) speeds up wallet scans.</span>
      </div>
    </Panel>
  )
}

function Chips({ list, onChange, placeholder, label }: { list: string[]; onChange: (l: string[]) => void; placeholder: string; label: string }) {
  const [v, setV] = useState('')
  return (
    <div className="row wrap">
      {list.map((x) => (
        <span key={x} className="chip">{x}<button className="linkish" style={{ display: 'inline' }} aria-label={`Remove ${x}`} onClick={() => onChange(list.filter((y) => y !== x))}><X size={10} /></button></span>
      ))}
      <input className="input" aria-label={label} style={{ height: '1.625rem', width: '8.75rem' }} placeholder={placeholder} value={v} onChange={(e) => setV(e.target.value.toUpperCase())}
        onKeyDown={(e) => { if (e.key === 'Enter' && v.trim()) { onChange([...new Set([...list, v.trim()])]); setV('') } }} />
    </div>
  )
}

export default function Settings() {
  const { settings: s } = useSettings()
  if (!s) return <Skel n={10} />
  const w = s.watchlist
  return (
    <div className="grid settings-grid" style={{ alignItems: 'start' }}>
      <nav className="col" style={{ position: 'sticky', top: 0, gap: '0.125rem' }} aria-label="Settings sections">
        <h1 className="h1" style={{ margin: '0 0 0.5rem' }}>Settings</h1>
        {SECTIONS.map(([id, l]) => (
          <a key={id} className="nav" href={`#set-${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: 'smooth' }) }}>{l}</a>
        ))}
      </nav>
      <div className="col">
        <div id="set-profile"><Profile s={s} /></div>
        <div id="set-display"><Display s={s} /></div>
        <div id="set-ai"><AISection s={s} /></div>
        <div id="set-elfa"><ElfaSection s={s} /></div>
        <div id="set-latency"><Latency s={s} /></div>
        <div id="set-sources"><Sources /></div>
        <div id="set-keys"><Keys /></div>
        <div id="set-watch">
          <Panel title="Watchlists" sub="Enter to add · drives tape, dashboard, scanner, news tagging">
            <div className="col">
              <div className="field"><span>Crypto (base symbols, streamed from the fastest venue that lists them)</span>
                <Chips label="Add crypto symbol" list={w.crypto} placeholder="+ BTC" onChange={(l) => saveSettings({ watchlist: { ...w, crypto: l } })} /></div>
              <div className="field"><span>Stocks / ETFs (Yahoo symbols: B3 uses .SA, e.g. PETR4.SA)</span>
                <Chips label="Add stock symbol" list={w.stocks} placeholder="+ NVDA" onChange={(l) => saveSettings({ watchlist: { ...w, stocks: l } })} /></div>
              <div className="field"><span>On-chain tokens (add from a wallet or a token page)</span>
                <div className="row wrap">
                  {(w.dex || []).map((d: any) => (
                    <span key={d.mint} className="chip">
                      <a href={`#/asset/dex/${d.mint}`}>{d.sym}</a>
                      <button className="linkish" style={{ display: 'inline' }} aria-label={`Remove ${d.sym}`} onClick={async () => { await del(`/api/watchlist?kind=dex&sym=${d.mint}`); loadSettings() }}><X size={10} /></button>
                    </span>
                  ))}
                  {!(w.dex || []).length && <span className="muted small">none yet</span>}
                </div>
              </div>
            </div>
          </Panel>
        </div>
        <div id="set-predict">
          <Panel title="Predictions" right={<Toggle label="Enable predictions" on={s.predict.enabled} onChange={(v) => saveSettings({ predict: { ...s.predict, enabled: v } })} />}>
            <div className="col">
              <div className="field"><span>Assets to forecast (crypto; each adds ~20s to retraining)</span>
                <Chips label="Add forecast asset" list={s.predict.symbols} placeholder="+ ETH" onChange={(l) => saveSettings({ predict: { ...s.predict, symbols: l } })} /></div>
              <div className="row small">
                <span className="muted">Horizons</span>
                {s.predict.horizons_h.map((h: number) => <span key={h} className="chip">{h >= 168 ? '7d' : `${h}h`}</span>)}
                <label className="row" style={{ marginLeft: '1rem' }}><span className="muted">Retrain every</span>
                  <input className="input num" style={{ width: '3.75rem', height: '1.625rem' }} type="number" defaultValue={s.predict.retrain_every_h} onBlur={(e) => saveSettings({ predict: { ...s.predict, retrain_every_h: +e.target.value } })} />
                  <span className="muted">h</span></label>
              </div>
            </div>
          </Panel>
        </div>
        <div id="set-risk">
          <Panel title="Risk profile" sub="used by the AI for sizing and by portfolio risk flags">
            <div className="row wrap" style={{ gap: '1rem' }}>
              <Seg label="Risk profile" value={s.risk.profile} options={[['conservative', 'Conservative'], ['moderate', 'Moderate'], ['aggressive', 'Aggressive']]}
                onChange={(v) => saveSettings({ risk: { ...s.risk, profile: v, ...({ conservative: { max_position_pct: 10, target_vol_pct: 25 }, moderate: { max_position_pct: 20, target_vol_pct: 40 }, aggressive: { max_position_pct: 35, target_vol_pct: 65 } } as any)[v] } })} />
              <label className="row small">Max single position <input className="input num" style={{ width: '3.75rem', height: '1.625rem' }} type="number" key={`m${s.risk.max_position_pct}`} defaultValue={s.risk.max_position_pct} onBlur={(e) => saveSettings({ risk: { ...s.risk, max_position_pct: +e.target.value } })} />%</label>
              <label className="row small">Target portfolio vol <input className="input num" style={{ width: '3.75rem', height: '1.625rem' }} type="number" key={`v${s.risk.target_vol_pct}`} defaultValue={s.risk.target_vol_pct} onBlur={(e) => saveSettings({ risk: { ...s.risk, target_vol_pct: +e.target.value } })} />%</label>
              <label className="row small">Hide wallet dust below $ <input className="input num" style={{ width: '4rem', height: '1.625rem' }} type="number" defaultValue={s.solana?.hide_dust_usd ?? 1} onBlur={(e) => saveSettings({ solana: { hide_dust_usd: +e.target.value } })} /></label>
            </div>
          </Panel>
        </div>
        <div id="set-about">
          <Panel title="About">
            <div className="kv" style={{ maxWidth: '45rem' }}>
              <span>App</span><span>Rook Market Analyser (package: rookery)</span>
              <span>Data & models</span><span>~/.local/share/rookery</span>
              <span>Settings & keys</span><span>~/.config/rookery</span>
              <span>Service</span><span>systemctl --user status rookery</span>
              <span>Logs</span><span>journalctl --user -u rookery -f</span>
            </div>
            <div className="tiny faint" style={{ marginTop: '0.625rem' }}>Local-first: keys, wallets, holdings and chats never leave this machine except the lookups needed to price them. Not financial advice: the models tell you when they have no edge, so believe them. 🐧</div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
