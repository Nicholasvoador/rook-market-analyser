import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { EdgeBadge, Err, Panel, Seg, Skel, Term } from '../components/ui'
import { post, useApi } from '../lib/api'
import { ago, isNum, num, pct, price, until } from '../lib/fmt'
import { assetHref } from '../lib/store'

function Reliability({ cal }: { cal: any }) {
  const W = 300
  const H = 220
  const pad = 30
  const X = (p: number) => pad + p * (W - pad - 8)
  const Y = (p: number) => H - pad - p * (H - pad - 8)
  const series: [string, string][] = [
    ['ensemble', 'var(--accent)'],
    ['llm', 'var(--ice)'],
  ]
  return (
    <svg width={W} height={H} style={{ display: 'block' }}>
      {[0, 0.25, 0.5, 0.75, 1].map((v) => (
        <g key={v}>
          <line x1={X(0)} x2={X(1)} y1={Y(v)} y2={Y(v)} stroke="var(--line)" />
          <text x={4} y={Y(v) + 3} fill="var(--faint)" fontSize="9" fontFamily="var(--mono)">{v}</text>
          <text x={X(v) - 6} y={H - 12} fill="var(--faint)" fontSize="9" fontFamily="var(--mono)">{v}</text>
        </g>
      ))}
      <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} stroke="var(--line-2)" strokeDasharray="4 4" />
      {series.map(([k, c]) => {
        const pts = (cal?.[k] || []).filter((b: any) => b.n > 0)
        return (
          <g key={k}>
            <polyline points={pts.map((b: any) => `${X(b.mean_p)},${Y(b.freq)}`).join(' ')} fill="none" stroke={c} strokeWidth="1.5" />
            {pts.map((b: any) => (
              <circle key={b.bucket} cx={X(b.mean_p)} cy={Y(b.freq)} r={Math.min(2 + Math.sqrt(b.n) / 2, 7)} fill={c} opacity={0.8}>
                <title>{`${k}: predicted ${(b.mean_p * 100).toFixed(0)}% → happened ${(b.freq * 100).toFixed(0)}% (n=${b.n})`}</title>
              </circle>
            ))}
          </g>
        )
      })}
      <text x={X(0.5)} y={H - 1} fill="var(--muted)" fontSize="9.5" textAnchor="middle">predicted P(up)</text>
    </svg>
  )
}

export default function Predictions() {
  const { data: cur, reload: rc } = useApi<any[]>('/api/predictions/current', 60000)
  const { data: sb, error, reload } = useApi<any>('/api/predictions/scoreboard', 60000)
  const [sym, setSym] = useState('BTC')
  const [busy, setBusy] = useState(false)
  const { data: hist } = useApi<any[]>(`/api/predictions/history?sym=${sym}`, 120000)
  const syms = [...new Set((cur || []).map((c) => c.sym))]

  const retrain = async () => {
    setBusy(true)
    await post('/api/predictions/retrain')
    setTimeout(() => {
      reload()
      rc()
      setBusy(false)
    }, 90000)
  }

  return (
    <div className="col">
      <div className="page-head">
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Predictions</h1>
          <div className="muted small" style={{ maxWidth: '56.25rem' }}>
            Every hour the ensemble (base rate · logistic · gradient boosting · online learner) issues P(price higher) for each horizon. Each forecast is graded against the real price when it matures. Graded results re-weight the ensemble, train the online model on live-only features (sentiment, F&amp;G, OI, long/short) and feed the AI's own calibration. Skill is always measured against the base rate.
          </div>
        </div>
        <span className="grow" />
        <button className="btn" onClick={retrain} disabled={busy}>
          <RefreshCw size={13} /> {busy ? 'retraining (~1 min)…' : 'Retrain now'}
        </button>
      </div>
      <Err e={error} />

      <Panel title="Live forecasts" flush>
        {!cur && <div style={{ padding: '0.75rem' }}><Skel n={6} /></div>}
        <table className="t">
          <thead>
            <tr>
              <th>Asset</th>
              <th>Horizon</th>
              <th className="num">P(up)</th>
              <th className="num">Base</th>
              <th className="num">Raw → cal</th>
              <th className="num">Shrink</th>
              <th>Components (base / logit / gbm / online)</th>
              <th className="num">10–90% cone</th>
              <th className="num">Backtest skill</th>
              <th>Edge</th>
              <th className="num">Resolves</th>
            </tr>
          </thead>
          <tbody>
            {(cur || []).map((p) => (
              <tr key={`${p.sym}${p.h}`} className="click" onClick={() => (location.hash = assetHref(p.sym, 'crypto'))}>
                <td><b style={{ fontWeight: 500 }}>{p.sym}</b> <span className="faint small">@ {price(p.price)}</span></td>
                <td className="num">{p.h >= 168 ? '7d' : `${p.h}h`}</td>
                <td className={`num ${p.p_up > 0.5 ? 'up' : 'down'}`}>{(p.p_up * 100).toFixed(1)}%</td>
                <td className="num muted">{(p.p_base * 100).toFixed(1)}%</td>
                <td className="num muted">{isNum(p.p_cal) ? `${(p.p_cal * 100).toFixed(1)}%` : '—'}</td>
                <td className="num muted">{isNum(p.shrink) ? `×${p.shrink.toFixed(2)}` : '—'}</td>
                <td className="num small">
                  {['base', 'logit', 'gbm', 'online'].map((m) => (
                    <span key={m} title={`${m}: weight ${((p.weights?.[m] ?? 0) * 100).toFixed(0)}%`} style={{ marginRight: '0.5rem', opacity: 0.4 + (p.weights?.[m] ?? 0) }}>
                      {isNum(p.components?.[m]) ? (p.components[m] * 100).toFixed(0) : '—'}
                      <span className="faint">·{((p.weights?.[m] ?? 0) * 100).toFixed(0)}w</span>
                    </span>
                  ))}
                </td>
                <td className="num small">{price(p.range80?.[0])} – {price(p.range80?.[1])}</td>
                <td className={`num ${p.skill_oos > 0 ? 'up' : 'down'}`} title={`effective samples ≈ ${p.n_eff}`}>{pct((p.skill_oos ?? 0) * 100, 2)}</td>
                <td><EdgeBadge edge={p.edge} /></td>
                <td className="num muted">{until(p.resolve_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.7fr) minmax(20.625rem,1fr)' }}>
        <Panel title="Scoreboard" sub="live (graded) vs backtest (purged walk-forward, prequential)" flush>
          {!sb && <div style={{ padding: '0.75rem' }}><Skel n={8} /></div>}
          <table className="t">
            <thead>
              <tr>
                <th>Asset</th>
                <th>H</th>
                <th>Source</th>
                <th className="num">Graded</th>
                <th className="num">Brier</th>
                <th className="num">Base Brier</th>
                <th className="num">Live skill</th>
                <th className="num">Hit rate</th>
                <th className="num">Backtest skill</th>
                <th className="num">n_eff</th>
                <th className="num"><Term k="coverage">Cone hit-rate</Term></th>
                <th className="num">Trained</th>
              </tr>
            </thead>
            <tbody>
              {(sb?.rows || []).map((r: any) => (
                <tr key={`${r.sym}${r.h}${r.source}`}>
                  <td>{r.sym}</td>
                  <td className="num">{r.h >= 168 ? '7d' : `${r.h}h`}</td>
                  <td><span className={`chip ${r.source === 'llm' ? 'ice' : 'accent'}`}>{r.source}</span></td>
                  <td className="num">{r.live.n}</td>
                  <td className="num">{num(r.live.brier, 4)}</td>
                  <td className="num muted">{num(r.live.brier_base, 4)}</td>
                  <td className={`num ${r.live.skill > 0 ? 'up' : r.live.skill < 0 ? 'down' : ''}`}>{isNum(r.live.skill) ? pct(r.live.skill * 100, 1) : '—'}</td>
                  <td className="num">{isNum(r.live.hit) ? `${(r.live.hit * 100).toFixed(0)}%` : '—'}</td>
                  <td className={`num ${r.oos?.skill_oos > 0 ? 'up' : r.oos?.skill_oos < 0 ? 'down' : ''}`}>{isNum(r.oos?.skill_oos) ? pct(r.oos.skill_oos * 100, 2) : '—'}</td>
                  <td className="num muted">{r.oos?.n_eff ?? '—'}</td>
                  <td className="num" title={r.width ? `cone width multiplier ×${r.width.toFixed(2)}` : ''}>
                    {r.coverage?.n ? <span className={Math.abs(r.coverage.rate - 0.8) <= 0.07 ? 'up' : 'warn'}>{(r.coverage.rate * 100).toFixed(0)}% <span className="faint">/ {r.coverage.n}</span></span> : '—'}
                  </td>
                  <td className="num muted">{r.oos?.trained_at ? ago(r.oos.trained_at) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="tiny faint" style={{ padding: '0.5rem 0.75rem' }}>
            <Term k="brier">Brier</Term> = mean squared error of probabilities (lower is better; 0.25 = coin flip). Skill = 1 − Brier / base-rate Brier. Overlapping horizons mean the effective sample size ≈ graded ÷ horizon hours.
            Cone hit-rate = share of realised prices inside the 10–90% cone (target 80%; the width self-corrects). {sb?.pending ?? 0} forecasts pending.
          </div>
        </Panel>
        <div className="col">
          <Panel title="Calibration" sub="does 60% happen 60% of the time?">
            <Reliability cal={sb?.calibration} />
            {sb && !(sb.calibration?.ensemble || []).some((b: any) => b.n > 0) && (
              <div className="tiny muted" style={{ marginTop: '-7.5rem', marginBottom: '6.25rem', textAlign: 'center' }}>
                No graded forecasts yet: the first 4h forecasts mature in a few hours.
              </div>
            )}
            <div className="legend" style={{ marginTop: '0.375rem' }}>
              <span><i style={{ background: 'var(--accent)' }} />models</span>
              <span><i style={{ background: 'var(--ice)' }} />AI calls</span>
              <span><i style={{ background: 'var(--line-2)' }} />perfect</span>
            </div>
          </Panel>
          <Panel title="AI track record">
            <div className="small">{sb?.llm?.text || '—'}</div>
            {isNum(sb?.llm?.alpha) && <div className="tiny muted" style={{ marginTop: '0.375rem' }}>This line is injected into every AI prompt so the model corrects its own over/under-confidence.</div>}
          </Panel>
        </div>
      </div>

      <Panel title="History" right={<Seg value={sym} options={syms.length ? syms : ['BTC']} onChange={setSym} />} flush>
        <div className="scroll" style={{ maxHeight: '26.25rem' }}>
          <table className="t">
            <thead>
              <tr>
                <th>Issued</th>
                <th>Source</th>
                <th>H</th>
                <th className="num">P(up)</th>
                <th className="num">From</th>
                <th className="num">To</th>
                <th>Outcome</th>
                <th className="num">Brier vs base</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {(hist || []).map((h) => (
                <tr key={h.id}>
                  <td className="num muted">{new Date(h.issued * 1000).toLocaleString('en-GB', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</td>
                  <td><span className={`chip ${h.source === 'llm' ? 'ice' : ''}`}>{h.source}</span></td>
                  <td className="num">{h.horizon_h}h</td>
                  <td className="num">{(h.p_up * 100).toFixed(0)}%</td>
                  <td className="num">{price(h.price)}</td>
                  <td className="num">{h.price_end ? price(h.price_end) : <span className="faint">in {until(h.resolve_at)}</span>}</td>
                  <td>{h.outcome == null ? <span className="faint">pending</span> : <span className={`chip ${h.outcome ? 'up' : 'down'}`}>{h.outcome ? 'up' : 'down'}</span>}</td>
                  <td className="num">{h.brier != null ? <span className={h.brier < h.brier_base ? 'up' : 'down'}>{h.brier.toFixed(3)} / {h.brier_base.toFixed(3)}</span> : '—'}</td>
                  <td className="small muted" style={{ whiteSpace: 'normal' }}>{h.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}
