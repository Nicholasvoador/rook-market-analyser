import { Fragment, useState } from 'react'
import { MessageSquare, Plus, Trash2 } from 'lucide-react'
import { Delta, Err, Panel, Seg, Skel, Term } from '../components/ui'
import Wallets from '../components/Wallets'
import { del, post, useApi } from '../lib/api'
import { isNum, money, num, pct, price, tone } from '../lib/fmt'
import { assetHref, go, useLive, useSettings } from '../lib/store'

const PALETTE = ['#ff8a3d', '#8cc8ff', '#2fbf88', '#e8b74a', '#f2566b', '#b9c0cb', '#c99bff', '#5fd4d4', '#f0a3c0', '#9fb86a']

function LivePrice({ r }: { r: any }) {
  const t = useLive(r.kind === 'crypto' ? r.symbol : null)
  return <>{price(t?.price ?? r.price)}</>
}

const hrefFor = (r: any) => (r.kind === 'dex' && r.mint ? `#/asset/dex/${r.mint}` : assetHref(r.symbol, r.kind))

function Corr({ c }: { c: { syms: string[]; matrix: number[][] } }) {
  const n = c.syms.length
  const cell = Math.min(46, Math.floor(300 / n))
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `3.75rem repeat(${n}, ${cell}px)`, gap: '0.125rem', fontSize: '0.6562rem' }}>
      <span />
      {c.syms.map((s) => (
        <span key={s} className="muted" style={{ textAlign: 'center', overflow: 'hidden' }}>{s.replace('.SA', '')}</span>
      ))}
      {c.matrix.map((row, i) => (
        <Fragment key={`r${i}`}>
          <span className="muted">{c.syms[i].replace('.SA', '')}</span>
          {row.map((v, j) => (
            <span
              key={`${i}-${j}`}
              className="num"
              title={`${c.syms[i]} × ${c.syms[j]}: ${v.toFixed(2)}`}
              style={{
                height: cell * 0.7,
                display: 'grid',
                placeItems: 'center',
                borderRadius: '0.1875rem',
                background: v >= 0 ? `rgba(255,138,61,${Math.abs(v) * 0.55})` : `rgba(140,200,255,${Math.abs(v) * 0.55})`,
              }}
            >
              {i === j ? '' : v.toFixed(2)}
            </span>
          ))}
        </Fragment>
      ))}
    </div>
  )
}

export default function Portfolio() {
  const { data, error, reload } = useApi<any>('/api/portfolio', 60000)
  const { settings, rate } = useSettings()
  const ccy = settings?.display?.currency || 'USD'
  const [f, setF] = useState({ symbol: '', kind: 'crypto', qty: '', avg_cost: '', currency: 'USD' })
  const [busy, setBusy] = useState(false)

  const add = async () => {
    if (!f.symbol || !f.qty) return
    setBusy(true)
    await post('/api/portfolio', { ...f, symbol: f.symbol.toUpperCase(), qty: +f.qty, avg_cost: +f.avg_cost || 0 })
    setF({ ...f, symbol: '', qty: '', avg_cost: '' })
    await reload()
    setBusy(false)
  }

  const rows = data?.rows || []
  const risk = data?.risk || {}
  return (
    <div className="col">
      <div className="page-head">
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Portfolio</h1>
          <div className="muted small">
            Risk profile <b>{settings?.risk?.profile}</b> · max position {settings?.risk?.max_position_pct}% · target vol {settings?.risk?.target_vol_pct}%
          </div>
        </div>
        <span className="grow" />
        <button className="btn primary" disabled={!rows.length} onClick={() => go(`ask?q=${encodeURIComponent('Review my portfolio: concentration, correlation, risk vs my profile, what is working, what is not, and specific rebalancing suggestions.')}`)}>
          <MessageSquare aria-hidden="true" /> AI portfolio review
        </button>
      </div>
      <Err e={error} />
      <div className="tiny faint" style={{ marginTop: '-0.375rem' }}>
        The AI review sees weights and percentages only (no amounts, no wallet addresses) unless you change it in Settings → AI.
      </div>

      <section className="panel">
        <div className="strip">
          <div className="stat"><span className="label">Value</span><span className="v">{money(data?.total_usd, ccy, rate, true)}</span><span className="d muted">{ccy === 'USD' ? `R$${num(data?.total_brl, 0)}` : `$${num(data?.total_usd, 0)}`}</span></div>
          <div className="stat"><span className="label">PnL</span><span className={`v ${tone(data?.pnl_pct)}`}>{pct(data?.pnl_pct)}</span><span className="d muted">cost {money(data?.cost_usd, ccy, rate, true)}</span></div>
          <div className="stat"><span className="label">In wallets</span><span className="v">{money(data?.wallet_value_usd, ccy, rate, true)}</span><span className="d muted">verified tokens only</span></div>
          <div className="stat"><span className="label"><Term k="vol">Ann. volatility</Term></span><span className={`v ${isNum(risk.port_vol) && risk.port_vol > (settings?.risk?.target_vol_pct || 40) * 1.25 ? 'down' : ''}`}>{num(risk.port_vol, 1)}%</span><span className="d muted">target {settings?.risk?.target_vol_pct}%</span></div>
          <div className="stat"><span className="label"><Term k="dd">Max drawdown 180d</Term></span><span className="v down">{num(risk.max_dd, 1)}%</span><span className="d muted">{risk.days ?? '—'} aligned days</span></div>
          <div className="stat"><span className="label">Vol scaling</span><span className="v">{isNum(risk.vol_scale) ? `×${risk.vol_scale.toFixed(2)}` : '—'}</span><span className="d muted">to hit target vol</span></div>
        </div>
      </section>

      {data?.flags?.length > 0 && (
        <Panel title="Risk flags">
          <ul className="small" style={{ margin: 0, paddingLeft: '1.125rem' }}>
            {data.flags.map((x: string) => <li key={x} className="warn" style={{ marginBottom: '0.25rem' }}>{x}</li>)}
          </ul>
        </Panel>
      )}

      <Wallets onChange={reload} />

      <Panel title="Add manual holding">
        <div className="row wrap">
          <input className="input" aria-label="Symbol" placeholder="Symbol (BTC, NVDA, PETR4.SA)" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} style={{ width: '13.125rem' }} />
          <Seg value={f.kind} options={[['crypto', 'Crypto'], ['stock', 'Stock/ETF']]} onChange={(v) => setF({ ...f, kind: v })} />
          <input className="input num" aria-label="Quantity" placeholder="Quantity" value={f.qty} onChange={(e) => setF({ ...f, qty: e.target.value })} style={{ width: '7.5rem' }} />
          <input className="input num" aria-label="Average cost per unit" placeholder="Avg cost / unit" value={f.avg_cost} onChange={(e) => setF({ ...f, avg_cost: e.target.value })} style={{ width: '8.75rem' }} />
          <Seg value={f.currency} options={['USD', 'BRL']} onChange={(v) => setF({ ...f, currency: v })} />
          <button className="btn primary" onClick={add} disabled={busy || !f.symbol || !f.qty}><Plus /> Add</button>
          <span className="tiny faint">Stored locally (~/.local/share/rookery). Nothing leaves your machine except price lookups.</span>
        </div>
      </Panel>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.8fr) minmax(20rem,1fr)' }}>
        <Panel title="Holdings" flush>
          {!data && <div style={{ padding: '0.75rem' }}><Skel n={5} /></div>}
          {data && !rows.length && <div className="empty">No holdings yet. Track a wallet or add a position above.</div>}
          {rows.length > 0 && (
            <>
              <div style={{ display: 'flex', height: '0.5rem', margin: '0.75rem', borderRadius: '0.25rem', overflow: 'hidden' }}>
                {rows.map((r: any, i: number) => (
                  <div key={r.id} title={`${r.symbol} ${r.weight.toFixed(1)}%`} style={{ width: `${r.weight}%`, background: PALETTE[i % PALETTE.length] }} />
                ))}
              </div>
              <table className="t">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Source</th>
                    <th className="num">Qty</th>
                    <th className="num">Price</th>
                    <th className="num">24h</th>
                    <th className="num">Value</th>
                    <th className="num">PnL</th>
                    <th className="num">Weight</th>
                    <th className="num"><Term k="vol">Vol</Term></th>
                    <th className="num"><Term k="riskshare">Risk share</Term></th>
                    <th className="num">Inv-vol wt.</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r: any, i: number) => (
                    <tr key={r.id} className="click" onClick={() => (location.hash = hrefFor(r))}>
                      <td><span className="row"><i aria-hidden="true" style={{ width: '0.5rem', height: '0.5rem', borderRadius: '0.125rem', background: PALETTE[i % PALETTE.length], display: 'inline-block' }} /> {r.symbol} {r.kind === 'dex' && <span className="flag">on-chain</span>}</span></td>
                      <td className="small muted">{r.source === 'wallet' ? r.wallet : 'manual'}</td>
                      <td className="num">{num(r.qty, r.qty < 10 ? 4 : 2)}</td>
                      <td className="num"><LivePrice r={r} /> <span className="faint tiny">{r.price_ccy !== 'USD' ? r.price_ccy : ''}</span></td>
                      <td className="num"><Delta v={r.chg24} /></td>
                      <td className="num">{money(r.value_usd, ccy, rate)}</td>
                      <td className="num">{r.source === 'wallet' ? <span className="faint">—</span> : <Delta v={r.pnl_pct} d={1} />}</td>
                      <td className={`num ${r.weight > (settings?.risk?.max_position_pct || 20) ? 'warn' : ''}`}>{num(r.weight, 1)}%</td>
                      <td className="num">{num(r.vol, 0)}%</td>
                      <td className="num">{num(r.risk_contrib, 0)}%</td>
                      <td className="num muted">{num(r.inv_vol_weight, 1)}%</td>
                      <td>
                        {r.source !== 'wallet' && (
                          <button className="btn ghost sm danger" aria-label={`Delete ${r.symbol} holding`} onClick={async (e) => { e.stopPropagation(); await del(`/api/portfolio/${r.id}`); reload() }}><Trash2 size={12} /></button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Panel>
        <Panel title={<Term k="corr">Correlation</Term>} sub="daily log returns, aligned dates · per asset">
          {risk.corr && risk.corr.syms.length > 1 ? <Corr c={risk.corr} /> : <div className="empty">Add 2+ holdings to see correlations</div>}
          <div className="tiny faint" style={{ marginTop: '0.625rem' }}>
            Inverse-volatility weights are a naive risk-balanced reference, not a recommendation. Risk share = each position's contribution to portfolio variance.
          </div>
        </Panel>
      </div>
    </div>
  )
}
