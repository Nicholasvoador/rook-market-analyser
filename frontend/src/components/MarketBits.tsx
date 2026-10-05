// Shared bits for market views: 52-week range bar, trend chip, Brazil rates card, per-asset performance card.
import { Delta, Panel, Skel, Term } from './ui'
import { isNum, num, pct, price } from '../lib/fmt'
import { fmtRet, PERIOD_LABEL, type MRow } from '../lib/markets'

export function RangeBar({ pos, lo, hi, unit }: { pos?: number | null; lo?: number | null; hi?: number | null; unit?: string | null }) {
  if (!isNum(pos)) return <span className="faint">—</span>
  const p = Math.max(0, Math.min(1, pos))
  const f = (x?: number | null) => (unit === 'yield' ? `${num(x, 2)}%` : price(x))
  return (
    <span className="range52" role="img" aria-label={`${Math.round(p * 100)}% of the way from the 52-week low ${f(lo)} to the high ${f(hi)}`} title={`52w low ${f(lo)} · high ${f(hi)}`}>
      <i style={{ left: `calc(${p * 100}% - 0.1875rem)` }} />
    </span>
  )
}

export function TrendChip({ t }: { t?: string | null }) {
  if (!t) return <span className="faint">—</span>
  const cls = t === 'up' ? 'up' : t === 'down' ? 'down' : ''
  const txt = t === 'up' ? '▲ up' : t === 'down' ? '▼ down' : '◆ mixed'
  return <span className={`chip ${cls}`}>{txt}</span>
}

/** br: undefined = loading, null = unavailable, object = data */
export function BrazilRates({ br }: { br: any }) {
  return (
    <Panel title="Brazil rates" sub="BCB · the bar every BRL investment has to clear">
      {br === undefined && <Skel n={4} />}
      {br === null && <div className="empty small">The central bank's API didn't answer just now; retrying automatically within a minute.</div>}
      {br && (
        <div className="col" style={{ gap: '0.75rem' }}>
          <div className="statgrid">
            <div className="stat">
              <div className="label"><Term k="selic">Selic</Term></div>
              <div className="v num">{num(br.selic, 2)}%</div>
              {isNum(br.selic_chg_1y_bp) && <div className="tiny muted">{br.selic_chg_1y_bp > 0 ? '+' : ''}{br.selic_chg_1y_bp} bp in 1 year</div>}
            </div>
            <div className="stat">
              <div className="label"><Term k="cdi">CDI</Term></div>
              <div className="v num">{num(br.cdi_aa, 2)}%</div>
              <div className="tiny muted">≈ {num(br.cdi_month, 2)}% a month</div>
            </div>
            <div className="stat">
              <div className="label"><Term k="ipca">IPCA 12m</Term></div>
              <div className="v num">{num(br.ipca12, 2)}%</div>
              {br.ipca_last && <div className="tiny muted">{br.ipca_last.month}: {pct(br.ipca_last.pct, 2)}</div>}
            </div>
            <div className="stat">
              <div className="label"><Term k="real_rate">Real rate</Term></div>
              <div className="v num">{num(br.real_rate, 1)}%</div>
              <div className="tiny muted">Selic minus inflation</div>
            </div>
          </div>
          <div className="small">
            CDI so far: <b className="num">{pct(br.cdi_acc?.ytd, 1)}</b> this year · <b className="num">{pct(br.cdi_acc?.y1, 1)}</b> over 12 months ·{' '}
            <b className="num">{pct(br.cdi_acc?.m1, 2)}</b> last month.
          </div>
        </div>
      )}
    </Panel>
  )
}

/** Asset page: returns in own currency, BRL and vs CDI + 52w range, volatility and trend. */
export function PerfCard({ r }: { r?: MRow | null }) {
  const ks = ['w1', 'm1', 'm3', 'ytd', 'y1']
  const showBrl = !!r?.brl && (r.ccy === 'USD' || r.ccy === 'USX')
  const showCdi = !!r?.xcdi
  return (
    <Panel title="Performance" sub={r ? `${r.ccy || ''} · daily closes` : ''}>
      {!r && <Skel n={5} />}
      {r && (
        <div className="col" style={{ gap: '0.75rem' }}>
          <table className="t">
            <thead>
              <tr>
                <th>Period</th>
                <th className="num">{r.unit === 'yield' ? 'Change' : r.ccy || 'Return'}</th>
                {showBrl && <th className="num">In BRL</th>}
                {showCdi && <th className="num"><Term k="xcdi">vs CDI</Term></th>}
              </tr>
            </thead>
            <tbody>
              {ks.map((k) => (
                <tr key={k}>
                  <td>{PERIOD_LABEL[k]}</td>
                  <td className="num">{r.unit === 'yield' ? fmtRet(r[k], 'yield') : <Delta v={r[k]} d={1} />}</td>
                  {showBrl && <td className="num"><Delta v={r.brl?.[k]} d={1} /></td>}
                  {showCdi && <td className="num"><Delta v={r.xcdi?.[k]} d={1} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="kv">
            <span><Term k="pos52">52-week range</Term></span>
            <span className="row"><RangeBar pos={r.pos52} lo={r.lo52} hi={r.hi52} unit={r.unit} /> {isNum(r.dd52) && r.unit !== 'yield' && <span className="small muted">{pct(r.dd52, 1)} from high</span>}</span>
            <span><Term k="trend">Daily trend</Term></span>
            <span><TrendChip t={r.trend} /></span>
            {isNum(r.vol30) && (
              <>
                <span><Term k="vol">Volatility (30d)</Term></span>
                <span className="num">{num(r.vol30, 0)}% a year</span>
              </>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}
