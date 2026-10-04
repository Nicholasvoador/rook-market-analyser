import { useMemo, useState } from 'react'
import { CBar, Delta, Err, Panel, ScoreChip, Seg, SentChip, Skel, Term, Verdict } from '../components/ui'
import { useApi } from '../lib/api'
import { ago, num, pct, price } from '../lib/fmt'
import { assetHref } from '../lib/store'

const STABLE = ['USDT', 'USDC', 'DAI', 'USDE', 'FDUSD', 'USDS', 'WBTC', 'STETH', 'WSTETH', 'WEETH', 'USD1', 'BSC-USD', 'WETH', 'CBBTC', 'SUSDE', 'BUIDL']

function SetupChips({ items }: { items: any[] }) {
  if (!items?.length) return <span className="faint">—</span>
  return (
    <span className="row" style={{ gap: '0.25rem', flexWrap: 'nowrap', whiteSpace: 'nowrap' }}>
      {items.slice(0, 1).map((a) => (
        <span key={`${a.id}${a.tf}`} className={`chip ${a.dir === 'bull' ? 'up' : a.dir === 'bear' ? 'down' : ''}`} title={`${a.explain}${a.stats?.n ? ` · historically ${(a.stats.hit * 100).toFixed(0)}% vs base ${(a.stats.base * 100).toFixed(0)}% (n=${a.stats.n}): ${a.stats.verdict}` : ''}`}>
          {a.label} · {a.tf}
        </span>
      ))}
      {items.length > 1 && <span className="tiny muted" title={items.slice(1).map((a) => `${a.label} · ${a.tf}`).join('\n')}>+{items.length - 1}</span>}
    </span>
  )
}

function Record() {
  const { data } = useApi<any>('/api/signals/record', 300000)
  const rows: any[] = data?.rows || []
  return (
    <Panel title="Live track record" sub={data ? `setups logged on your watchlist · ${data.pending} awaiting their outcome` : ''} flush>
      <table className="t">
        <thead><tr><th>Setup</th><th>TF</th><th className="num">Fired</th><th className="num">Worked</th><th className="num">Avg move</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.setup}${r.tf}`}>
              <td><span className={r.dir === 'bull' ? 'up' : r.dir === 'bear' ? 'down' : ''}>●</span> {r.label}</td>
              <td className="small muted">{r.tf}</td>
              <td className="num">{r.n}</td>
              <td className="num">{r.hit != null ? `${(r.hit * 100).toFixed(0)}%` : '—'}</td>
              <td className="num"><Delta v={r.avg_ret * 100} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && !rows.length && (
        <div className="empty small">
          Nothing graded yet. Every new setup on your watchlist is logged and checked after its horizon (24h, or 7d for daily), so this table fills up on its own.
        </div>
      )}
      {(data?.recent || []).length > 0 && (
        <>
          <div className="label" style={{ padding: '0.625rem 0.75rem 0.25rem' }}>Recently fired</div>
          <div className="scroll" style={{ maxHeight: '16rem' }}>
            {data.recent.slice(0, 20).map((e: any) => (
              <a key={e.id} className="newsi" href={assetHref(e.sym, e.kind)} style={{ display: 'flex', gap: '0.5rem' }}>
                <b style={{ width: '4rem', fontWeight: 500 }}>{e.sym.replace('.SA', '')}</b>
                <span className="small grow">{e.label} <span className="faint">({e.tf})</span></span>
                <span className="small num">{e.ret != null ? <Delta v={e.ret * 100} /> : <span className="faint">{ago(e.ts)} ago</span>}</span>
              </a>
            ))}
          </div>
        </>
      )}
    </Panel>
  )
}

export default function Signals() {
  const [scope, setScope] = useState<'watch' | 'top'>('watch')
  const { data: mk } = useApi<any>('/api/crypto/markets?n=60', 300000)
  const topSyms = useMemo(() => (mk?.rows || []).filter((r: any) => !STABLE.includes(r.sym)).slice(0, 35).map((r: any) => r.sym), [mk])
  const path = scope === 'watch' ? '/api/signals?deep=true' : topSyms.length ? `/api/signals?kind=crypto&deep=true&syms=${topSyms.join(',')}` : null
  const { data, error, loading } = useApi<any[]>(path, 120000)
  const [kind, setKind] = useState<'all' | 'crypto' | 'stock'>('all')
  const [sortK, setSortK] = useState<'score' | 'rsi' | 'sent' | 'setups'>('score')
  const rows = useMemo(() => {
    const r = (data || []).filter((x) => kind === 'all' || x.kind === kind)
    const v = (x: any) =>
      sortK === 'score' ? x.score : sortK === 'rsi' ? x.t1h?.rsi ?? 50 : sortK === 'setups' ? (x.setups || []).length : x.sentiment?.sentiment ?? 0
    return r.sort((a, b) => v(b) - v(a))
  }, [data, kind, sortK])
  const events = rows.flatMap((r) => r.notes.map((n: string) => ({ sym: r.sym, kind: r.kind, n })))
  const allSetups = rows.flatMap((r) => (r.setups || []).map((a: any) => ({ ...a, sym: r.sym, kind: r.kind })))

  return (
    <div className="col">
      <div className="page-head">
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Signals</h1>
          <div className="muted small">
            <Term k="score">Rook score</Term> = daily trend 25% · 4h trend 15% · 1h trend 15% · momentum 15% · news tone 15% · positioning 15% (contrarian on funding and long/short).
          </div>
        </div>
        <span className="grow" />
        <Seg label="Universe" value={scope} options={[['watch', 'Watchlist'], ['top', 'Top 35 crypto']]} onChange={setScope} />
        <Seg label="Asset type" value={kind} options={[['all', 'All'], ['crypto', 'Crypto'], ['stock', 'Stocks']]} onChange={setKind} />
        <Seg label="Sort by" value={sortK} options={[['score', 'Score'], ['setups', 'Setups'], ['rsi', 'RSI'], ['sent', 'Tone']]} onChange={setSortK} />
      </div>
      <div className="split">
        <Panel className="split-main" flush title="Scanner" sub={loading ? 'scanning…' : `${rows.length} assets`}>
          <Err e={error} />
          {!data && <div style={{ padding: '0.75rem' }}><Skel n={12} h={18} /></div>}
          <div className="scroll">
            <table className="t">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th className="num">Price</th>
                  <th>Score</th>
                  <th>Trend 1d</th>
                  <th>Trend 4h</th>
                  <th>Momentum</th>
                  <th className="num"><Term k="rsi">RSI</Term> 1h</th>
                  <th className="num">RSI 1d</th>
                  <th className="num"><Term k="funding">Funding</Term></th>
                  <th>Tone</th>
                  <th><Term k="setup">Setups</Term></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.sym} className="click" onClick={() => (location.hash = assetHref(r.sym, r.kind))}>
                    <td>
                      <b style={{ fontWeight: 500 }}>{r.sym.replace('.SA', '')}</b> <span className="faint tiny">{r.kind}</span>
                    </td>
                    <td className="num">{price(r.t1h?.price ?? r.t1d?.price)}</td>
                    <td className="nowrap">
                      <ScoreChip score={r.score} label={r.label} /> <span className="small muted">{r.label}</span>
                    </td>
                    <td style={{ width: '5rem' }}><CBar v={r.components.trend_d} /></td>
                    <td style={{ width: '5rem' }}><CBar v={r.components.trend_4h} /></td>
                    <td style={{ width: '5rem' }}><CBar v={r.components.momentum} /></td>
                    <td className={`num ${r.t1h?.rsi >= 70 ? 'down' : r.t1h?.rsi <= 30 ? 'up' : ''}`}>{num(r.t1h?.rsi, 0)}</td>
                    <td className={`num ${r.t1d?.rsi >= 70 ? 'down' : r.t1d?.rsi <= 30 ? 'up' : ''}`}>{num(r.t1d?.rsi, 0)}</td>
                    <td className="num">{r.deriv ? `${r.deriv.fund8h.toFixed(4)}%` : '—'}</td>
                    <td><SentChip v={r.sentiment?.sentiment} n={r.sentiment?.mentions} /></td>
                    <td><SetupChips items={r.setups} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <div className="split-side">
          <Panel title="Setups firing now" sub="each with its historical hit rate on that asset" flush>
            <div className="scroll" style={{ maxHeight: '28rem' }}>
              {allSetups.map((a, i) => (
                <a key={i} className="newsi" href={assetHref(a.sym, a.kind)} style={{ display: 'block' }}>
                  <div className="row">
                    <b style={{ width: '4rem', fontWeight: 500 }}>{a.sym.replace('.SA', '')}</b>
                    <span className={`small ${a.dir === 'bull' ? 'up' : a.dir === 'bear' ? 'down' : ''}`}>{a.label}</span>
                    <span className="chip">{a.tf}</span>
                    <span className="grow" />
                    <Verdict v={a.stats?.verdict} />
                  </div>
                  {a.stats?.n > 0 && (
                    <div className="tiny muted" style={{ marginTop: '0.125rem', paddingLeft: '4.5rem' }}>
                      {(a.stats.hit * 100).toFixed(0)}% vs base {(a.stats.base * 100).toFixed(0)}% over {a.stats.horizon} · n={a.stats.n} · avg {pct(a.stats.avg * 100, 2)}
                    </div>
                  )}
                </a>
              ))}
              {data && !allSetups.length && <div className="empty">No setups firing right now.</div>}
            </div>
          </Panel>
          <Panel title="Events" sub="crosses · extremes · crowding" flush>
            <div className="scroll" style={{ maxHeight: '20rem' }}>
              {events.map((e, i) => (
                <a key={i} className="newsi" href={assetHref(e.sym, e.kind)} style={{ display: 'flex' }}>
                  <b style={{ width: '4rem', fontWeight: 500, flex: 'none' }}>{e.sym.replace('.SA', '')}</b>
                  <span className="small">{e.n}</span>
                </a>
              ))}
              {data && !events.length && <div className="empty">No notable events right now</div>}
            </div>
          </Panel>
          <Record />
        </div>
      </div>
      <div className="tiny faint">
        Scores describe current conditions; they are not forecasts. Setup statistics compare each pattern's past hit rate with the plain base rate on the same asset and timeframe:
        "no clear edge" is the most common, honest result.
      </div>
    </div>
  )
}
