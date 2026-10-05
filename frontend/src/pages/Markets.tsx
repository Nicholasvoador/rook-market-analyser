import { useEffect, useMemo, useRef, useState } from 'react'
import { ColorType, createChart, LineSeries, LineStyle, type IChartApi } from 'lightweight-charts'
import { BrazilRates, RangeBar, TrendChip } from '../components/MarketBits'
import { CBar, Delta, Err, Panel, Readout, Seg, Skel, Spark, Term } from '../components/ui'
import { useApi } from '../lib/api'
import { ago, isNum, num, price } from '../lib/fmt'
import { backdrop, fmtRet, KIND_LABEL, perfLines, RET_COLS, SHORT, type Mode, type MRow, type Period } from '../lib/markets'
import { assetHref, useSettings } from '../lib/store'

const TABS: [string, string][] = [
  ['overview', 'Overview'],
  ['crypto', 'Crypto'],
  ['stocks', 'Stocks'],
  ['commodities', 'Commodities'],
  ['indices', 'Indices'],
  ['fx', 'FX'],
  ['rates', 'Rates'],
  ['etfs', 'ETFs'],
]
const BLURB: Record<string, string> = {
  crypto: 'Your crypto watchlist and the top coins by market cap, measured from exchange daily candles. Live prices stream from the fastest exchange.',
  stocks: 'Your watchlist, US mega caps, crypto-linked stocks and the largest Brazilian (B3) names.',
  commodities: 'Continuous front-month futures for metals, energy, grains, softs and livestock.',
  indices: 'Main equity indices across the Americas, Europe and Asia, plus the VIX volatility index.',
  fx: 'The dollar against the majors and the real. A rising USD/BRL means a weaker real.',
  rates: 'Treasury yields move in basis points (1 bp = 0.01%). Brazil rates come from the central bank.',
  etfs: 'Broad market, bond, commodity, crypto and US sector ETFs, plus BRL-listed ETFs.',
}
const COLORS: Record<string, string> = {
  'BTC-USD': '#f7931a',
  'ETH-USD': '#9b8cff',
  '^GSPC': '#8cc8ff',
  '^NDX': '#2fbf88',
  'GC=F': '#e8b74a',
  '^BVSP': '#ff7eb6',
  CDI: '#b9c0cb',
}

const hrefFor = (r: MRow) => assetHref(r.sym, r.kind === 'crypto' ? 'crypto' : 'stock')

// ------------------------------------------------------------------ board table
function RetCell({ r, k, mode }: { r: MRow; k: string; mode: Mode }) {
  if (r.unit === 'yield') {
    const v = r[k]
    return <span className={`num dir ${isNum(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : '') : ''}`}>{fmtRet(v, 'yield')}</span>
  }
  const own = isNum(r[k]) ? r[k] : null
  const m = mode === 'local' ? null : mode === 'brl' ? r.brl : r.xcdi
  if (m && isNum(m[k])) return <Delta v={m[k]} d={1} />
  if (mode !== 'local' && own != null) return <span title="in the asset's own currency (no BRL / CDI conversion for this asset)"><Delta v={own} d={1} className="muted-delta" /></span>
  return <Delta v={own} d={1} />
}

type SortK = { k: string; dir: 1 | -1 } | null

function Th({ k, sort, onSort, children, className = 'num' }: { k: string; sort: SortK; onSort: (s: SortK) => void; children: React.ReactNode; className?: string }) {
  const on = sort?.k === k
  return (
    <th className={className} aria-sort={on ? (sort!.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className="th-sort" onClick={() => onSort(on ? (sort!.dir === -1 ? { k, dir: 1 } : null) : { k, dir: k === 'label' ? 1 : -1 })}>
        {children}
        <span aria-hidden="true">{on ? (sort!.dir === -1 ? ' ▾' : ' ▴') : ''}</span>
      </button>
    </th>
  )
}

function Board({ tab, mode }: { tab: string; mode: Mode }) {
  const { data, error, loading } = useApi<any>(`/api/markets/board?tab=${tab}`, tab === 'crypto' ? 30000 : 60000)
  const [sort, setSort] = useState<SortK>(null)
  useEffect(() => setSort(null), [tab])
  const val = (r: MRow, k: string) => {
    if (k === 'label') return r.label
    if (k === 'chg1d' || k === 'rsi' || k === 'pos52' || k === 'vol30') return r[k]
    if (r.unit === 'yield' || mode === 'local') return r[k]
    const m = mode === 'brl' ? r.brl : r.xcdi
    return m && isNum(m[k]) ? m[k] : r[k]
  }
  const groups: { name: string; rows: MRow[] }[] = useMemo(() => {
    const g = data?.groups || []
    if (!sort) return g
    const seen = new Set<string>()
    const flat: MRow[] = []
    for (const x of g)
      for (const r of x.rows)
        if (!seen.has(r.sym)) {
          seen.add(r.sym)
          flat.push(r)
        }
    flat.sort((a, b) => {
      const va = val(a, sort.k)
      const vb = val(b, sort.k)
      if (va == null) return 1
      if (vb == null) return -1
      return (typeof va === 'string' ? va.localeCompare(vb) : va - vb) * sort.dir
    })
    return [{ name: '', rows: flat }]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, sort, mode])

  const yieldTab = tab === 'rates'
  const ncols = 12

  return (
    <div className="col">
      {tab === 'rates' && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 28rem), 1fr))', alignItems: 'start' }}>
          <BrazilRates br={data ? data.brazil ?? null : undefined} />
          <UsCurve c={data?.us_curve} />
        </div>
      )}
      <Panel
        flush
        title={TABS.find(([k]) => k === tab)?.[1]}
        sub={data ? `${groups.reduce((n, g) => n + g.rows.length, 0)} assets · updated ${ago(data.updated)} ago${loading ? ' · refreshing' : ''}` : 'loading'}
        right={sort && <button className="btn ghost sm" onClick={() => setSort(null)}>Back to groups</button>}
      >
        <Err e={error} />
        {!data && <div style={{ padding: '0.75rem' }}><Skel n={10} h={20} /></div>}
        {data && (
          <table className="t mkt">
            <thead>
              <tr>
                <Th k="label" sort={sort} onSort={setSort} className="">Asset</Th>
                <th className="num">{yieldTab ? 'Yield' : 'Price'}</th>
                <Th k="chg1d" sort={sort} onSort={setSort}>1D</Th>
                {RET_COLS.map(([k, l]) => <Th key={k} k={k} sort={sort} onSort={setSort}>{l}</Th>)}
                <Th k="pos52" sort={sort} onSort={setSort} className=""><Term k="pos52">52w range</Term></Th>
                <Th k="rsi" sort={sort} onSort={setSort}><Term k="rsi">RSI</Term></Th>
                <th><Term k="trend">Trend</Term></th>
                {!yieldTab && <Th k="vol30" sort={sort} onSort={setSort}><Term k="vol">Vol</Term></Th>}
                <th>3 months</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => [
                g.name ? (
                  <tr key={`g:${g.name}`} className="grp">
                    <th colSpan={ncols} scope="colgroup">{g.name}</th>
                  </tr>
                ) : null,
                ...g.rows.map((r) => (
                  <tr key={`${g.name}:${r.sym}`} className="click" onClick={() => (location.hash = hrefFor(r))}>
                    <td>
                      <a href={hrefFor(r)} className="mkt-name" onClick={(e) => e.stopPropagation()}>
                        <b>{r.label}</b>
                        <span className="faint tiny"> {r.sym.replace('.SA', '')}</span>
                      </a>
                    </td>
                    <td className="num nowrap">
                      {yieldTab ? `${num(r.price, 3)}%` : price(r.price)}
                      {r.ccy && r.ccy !== 'USD' && !yieldTab && r.kind !== 'fx' && <span className="faint tiny" title={r.ccy === 'USX' ? 'US cents' : undefined}> {r.ccy === 'USX' ? 'US¢' : r.ccy}</span>}
                    </td>
                    <td className="num">{yieldTab ? <span className="muted small">{isNum(r.chg1d) ? `${r.chg1d > 0 ? '+' : ''}${r.chg1d.toFixed(1)}%` : '—'}</span> : <Delta v={r.chg1d} />}</td>
                    {RET_COLS.map(([k]) => (
                      <td key={k} className="num"><RetCell r={r} k={k} mode={mode} /></td>
                    ))}
                    <td><RangeBar pos={r.pos52} lo={r.lo52} hi={r.hi52} unit={r.unit} /></td>
                    <td className={`num ${isNum(r.rsi) && r.rsi >= 70 ? 'down' : isNum(r.rsi) && r.rsi <= 30 ? 'up' : ''}`}>{num(r.rsi, 0)}</td>
                    <td><TrendChip t={r.trend} /></td>
                    {!yieldTab && <td className="num muted">{isNum(r.vol30) ? `${num(r.vol30, 0)}%` : '—'}</td>}
                    <td aria-hidden="true">
                      <Spark data={r.spark || []} w={96} h={24} color={(r.spark?.[r.spark.length - 1] ?? 0) >= (r.spark?.[0] ?? 0) ? 'var(--up)' : 'var(--down)'} />
                    </td>
                  </tr>
                )),
              ])}
            </tbody>
          </table>
        )}
      </Panel>
      <div className="tiny faint">
        {BLURB[tab]} Returns compare today's price with the close 7 / 30 / 365 days ago (YTD: last close of last year).
        {mode !== 'local' && !['fx', 'rates'].includes(tab) &&
          ` ${mode === 'brl' ? 'In BRL converts USD-priced assets with USD/BRL over the same window.' : `vs CDI is the excess return in BRL over CDI for the same window (CDI YTD ${num(data?.cdi_acc?.ytd, 1)}%).`} Greyed values are in the asset's own currency.`}
        {' '}Sources: {tab === 'crypto' ? 'exchange candles (latency-ranked), ' : ''}Yahoo Finance, BCB.
      </div>
    </div>
  )
}

function UsCurve({ c }: { c: any }) {
  const pts: [string, number | null][] = c ? [['3M', c['3m']], ['2Y', c['2y']], ['5Y', c['5y']], ['10Y', c['10y']], ['30Y', c['30y']]] : []
  const vals = pts.map(([, v]) => v).filter(isNum)
  const mn = Math.min(...vals) - 0.15
  const mx = Math.max(...vals) + 0.15
  const W = 320
  const H = 120
  const x = (i: number) => 24 + (i * (W - 48)) / Math.max(1, pts.length - 1)
  const y = (v: number) => H - 22 - ((v - mn) / (mx - mn || 1)) * (H - 40)
  const inv = isNum(c?.spread_2s10s) && c.spread_2s10s < 0
  return (
    <Panel title="US Treasury curve" sub={c?.date ? `Treasury · ${c.date}` : ''}>
      {!c && <Skel n={4} />}
      {c && (
        <div className="col" style={{ gap: '0.5rem' }}>
          <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: '30rem', height: 'auto' }} role="img"
            aria-label={`Yield curve: ${pts.map(([l, v]) => `${l} ${num(v, 2)}%`).join(', ')}`}>
            <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={pts.filter(([, v]) => isNum(v)).map(([, v], i) => `${x(i)},${y(v as number)}`).join(' ')} />
            {pts.map(([l, v], i) => isNum(v) && (
              <g key={l}>
                <circle cx={x(i)} cy={y(v)} r="3" fill="var(--accent)" />
                <text x={x(i)} y={y(v) - 8} textAnchor="middle" fontSize="10" fill="var(--ink-2)">{v.toFixed(2)}</text>
                <text x={x(i)} y={H - 4} textAnchor="middle" fontSize="10" fill="var(--muted)">{l}</text>
              </g>
            ))}
          </svg>
          <div className="small">
            2s10s spread <b className="num">{isNum(c.spread_2s10s) ? `${c.spread_2s10s > 0 ? '+' : ''}${(c.spread_2s10s * 100).toFixed(0)} bp` : '—'}</b>:{' '}
            {inv ? 'inverted (short rates above long rates), historically a late-cycle warning.' : 'normal (long rates above short rates).'}
          </div>
        </div>
      )}
    </Panel>
  )
}

// ------------------------------------------------------------------ overview
function PerfChart({ lines, hidden }: { lines: ReturnType<typeof perfLines>; hidden: Set<string> }) {
  const el = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!el.current) return
    const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1
    const chart: IChartApi = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#828c9b', fontFamily: 'JetBrains Mono, monospace', fontSize: Math.round(12 * scale), attributionLogo: false },
      grid: { vertLines: { color: '#151a20' }, horzLines: { color: '#151a20' } },
      rightPriceScale: { borderColor: '#1f252e' },
      timeScale: { borderColor: '#1f252e' },
      localization: { priceFormatter: (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%` },
    })
    for (const l of lines) {
      if (hidden.has(l.key) || !l.points.length) continue
      const s = chart.addSeries(LineSeries, {
        color: COLORS[l.key] || '#ccc',
        lineWidth: l.key === 'CDI' ? 1 : 2,
        lineStyle: l.key === 'CDI' ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        title: SHORT[l.key] || l.key,
      })
      s.setData(l.points as any)
    }
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [lines, hidden])
  return <div ref={el} style={{ height: '22rem' }} role="img" aria-label={`Relative performance: ${lines.filter((l) => !hidden.has(l.key)).map((l) => `${l.label} ${isNum(l.last) ? `${l.last > 0 ? '+' : ''}${l.last.toFixed(1)}%` : 'n/a'}`).join(', ')}`} />
}

function PerfPanel({ perf }: { perf: any }) {
  const { settings } = useSettings()
  const [period, setPeriod] = useState<Period>('ytd')
  const [ccy, setCcy] = useState<'USD' | 'BRL'>(() => (settings?.profile?.home_currency === 'BRL' ? 'BRL' : 'USD'))
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const lines = useMemo(() => (perf ? perfLines(perf, period, ccy) : []), [perf, period, ccy])
  const sorted = [...lines].sort((a, b) => (b.last ?? -1e9) - (a.last ?? -1e9))
  return (
    <Panel title="Relative performance" sub={ccy === 'BRL' ? 'in BRL, with CDI as the benchmark' : 'in USD'}
      right={<>
        <Seg label="Period" value={period} options={[['m1', '1M'], ['m3', '3M'], ['ytd', 'YTD'], ['y1', '1Y']]} onChange={setPeriod} />
        <Seg label="Currency" value={ccy} options={['USD', 'BRL']} onChange={setCcy} />
      </>}>
      {!perf && <Skel n={8} />}
      {perf && (
        <>
          <div className="legend-btns" role="group" aria-label="Show or hide lines">
            {sorted.map((l) => (
              <button key={l.key} className={`lg ${hidden.has(l.key) ? 'off' : ''}`} aria-pressed={!hidden.has(l.key)}
                onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(l.key)) n.delete(l.key); else n.add(l.key); return n })}>
                <i style={{ background: COLORS[l.key] || '#ccc' }} aria-hidden="true" />
                {l.label} <span className={`num dir ${isNum(l.last) ? (l.last > 0 ? 'up' : l.last < 0 ? 'down' : '') : ''}`}>{isNum(l.last) ? `${l.last > 0 ? '+' : ''}${l.last.toFixed(1)}%` : '—'}</span>
              </button>
            ))}
          </div>
          <PerfChart lines={lines} hidden={hidden} />
        </>
      )}
    </Panel>
  )
}

function heat(v: number | null) {
  if (!isNum(v)) return undefined
  const a = Math.min(1, Math.abs(v)) * 0.55
  return v >= 0 ? `rgba(140,200,255,${a})` : `rgba(255,138,61,${a})`
}

function CorrPanel({ d }: { d: any }) {
  const names: string[] = d?.names || []
  return (
    <Panel title={<Term k="corr">Correlations</Term>} sub="90 trading days of daily returns">
      {!d && <Skel n={8} />}
      {d && (
        <>
          <table className="t heat">
            <thead>
              <tr>
                <th><span className="sr-only">Asset</span></th>
                {names.map((n) => <th key={n} scope="col" className="num">{SHORT[n] || n}</th>)}
              </tr>
            </thead>
            <tbody>
              {names.map((a, i) => (
                <tr key={a}>
                  <th scope="row">{SHORT[a] || a}</th>
                  {names.map((b, j) => {
                    const v = d.matrix[i][j]
                    return (
                      <td key={b} className="num" style={{ background: i === j ? 'var(--panel-2)' : heat(v) }} title={`${d.labels[a]} vs ${d.labels[b]}: ${isNum(v) ? v.toFixed(2) : 'n/a'}`}>
                        {i === j ? '' : isNum(v) ? v.toFixed(2).replace('0.', '.').replace('-.', '−.') : '—'}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="tiny faint" style={{ marginTop: '0.5rem' }}>
            Blue = move together, orange = move opposite, pale = unrelated. US 10Y uses yield changes. Correlations shift: check the 30-day column next door.
          </div>
        </>
      )}
    </Panel>
  )
}

function BtcDrivers({ btc }: { btc: any[] }) {
  return (
    <Panel title="What moves Bitcoin" sub="correlation of daily returns: 30 days vs 90 days">
      {!btc && <Skel n={8} />}
      {btc && (
        <table className="t">
          <thead><tr><th>vs BTC</th><th className="num">30d</th><th style={{ width: '40%' }} /><th className="num">90d</th></tr></thead>
          <tbody>
            {[...btc].sort((a, b) => Math.abs(b.c90 ?? 0) - Math.abs(a.c90 ?? 0)).map((b) => (
              <tr key={b.sym}>
                <td>{b.label}</td>
                <td className="num">{isNum(b.c30) ? b.c30.toFixed(2) : '—'}</td>
                <td><CBar v={b.c30} /></td>
                <td className="num muted">{isNum(b.c90) ? b.c90.toFixed(2) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}

function MoverList({ rows }: { rows: any[] }) {
  return (
    <table className="t">
      <tbody>
        {rows.map((r) => (
          <tr key={r.sym} className="click" onClick={() => (location.hash = assetHref(r.sym, 'stock'))}>
            <td><a href={assetHref(r.sym, 'stock')} onClick={(e) => e.stopPropagation()}>{r.label}</a> <span className="faint tiny">{KIND_LABEL[r.kind] || r.kind}</span></td>
            <td className="num"><Delta v={r.chg1d} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Movers({ m }: { m: any }) {
  return (
    <Panel title="Biggest moves today" sub="stocks · commodities · indices · ETFs">
      {!m && <Skel n={8} />}
      {m && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 14rem), 1fr))', gap: '0.75rem' }}>
          <div><div className="label" style={{ marginBottom: '0.25rem' }}>▲ Up</div><MoverList rows={m.up} /></div>
          <div><div className="label" style={{ marginBottom: '0.25rem' }}>▼ Down</div><MoverList rows={m.down} /></div>
        </div>
      )}
    </Panel>
  )
}

function Overview() {
  const { data, error } = useApi<any>('/api/markets/overview', 120000)
  return (
    <div className="col">
      <Err e={error} />
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 28rem), 1fr))', alignItems: 'start' }}>
        <Panel title="Cross-asset backdrop" sub="stocks · dollar · rates · gold & oil · Bitcoin · Brazil">
          {data ? <Readout ro={{ bottom_line: backdrop(data.readout || []), lines: data.readout }} /> : <Skel n={6} />}
        </Panel>
        <BrazilRates br={data ? data.brazil ?? null : undefined} />
      </div>
      <PerfPanel perf={data?.perf} />
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 26rem), 1fr))', alignItems: 'start' }}>
        <CorrPanel d={data} />
        <BtcDrivers btc={data?.btc} />
        <Movers m={data?.movers} />
      </div>
      <div className="tiny faint">Daily closes from Yahoo Finance (BTC/ETH included for like-for-like comparison) and the Central Bank of Brazil. Refreshed every 15 minutes.</div>
    </div>
  )
}

// ------------------------------------------------------------------ page
export default function Markets({ tab }: { tab?: string }) {
  const { settings } = useSettings()
  const t = TABS.some(([k]) => k === tab) ? tab! : localStorage.getItem('rook.mtab') || 'overview'
  useEffect(() => localStorage.setItem('rook.mtab', t), [t])
  const [mode, setModeS] = useState<Mode>(() => (localStorage.getItem('rook.mmode') as Mode) || (settings?.profile?.home_currency === 'BRL' ? 'brl' : 'local'))
  const setMode = (m: Mode) => {
    setModeS(m)
    localStorage.setItem('rook.mmode', m)
  }
  const showMode = !['overview', 'fx', 'rates'].includes(t)
  return (
    <div className="col">
      <div className="page-head">
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Markets</h1>
          <div className="muted small">The same metrics for every asset class. Click any row for the full chart, levels, setups and the plain-language readout.</div>
        </div>
        <span className="grow" />
        {showMode && (
          <div className="row small">
            <span className="muted">Returns</span>
            <Seg label="Show returns" value={mode} options={[['local', 'Own currency'], ['brl', 'In BRL'], ['xcdi', 'vs CDI']]} onChange={setMode} />
          </div>
        )}
      </div>
      <nav className="tabs" aria-label="Market sections">
        {TABS.map(([k, l]) => (
          <a key={k} className={`tab ${k === t ? 'on' : ''}`} aria-current={k === t ? 'page' : undefined} href={`#/markets/${k}`}>{l}</a>
        ))}
      </nav>
      {t === 'overview' ? <Overview /> : <Board tab={t} mode={mode} />}
    </div>
  )
}
