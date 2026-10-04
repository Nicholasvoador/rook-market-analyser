import { useEffect, useState } from 'react'
import { Bell, ExternalLink, MessageSquare, Plus, Sparkles } from 'lucide-react'
import Chart, { type ChartInfo, type Cone, type Ind, type PriceLine } from '../components/Chart'
import OrderBook from '../components/OrderBook'
import { CBar, Delta, EdgeBadge, Err, Flash, Levels, Panel, Readout, ScoreChip, Seg, SetupList, Skel, Spark, Term } from '../components/ui'
import { api, post, useApi } from '../lib/api'
import { ago, big, isNum, num, pct, price, tone, until } from '../lib/fmt'
import { go, loadSettings, useLive, useSettings } from '../lib/store'
import { NewsList } from './Dashboard'

type Kind = 'crypto' | 'stock' | 'dex'
const IVS = ['1m', '5m', '15m', '1h', '4h', '1d', '1w']
const DEX_IVS = ['1m', '5m', '15m', '1h', '4h', '1d']
const COMP_LABEL: Record<string, [string, string]> = {
  trend_d: ['Trend (daily EMAs)', 'ema'],
  trend_4h: ['Trend (4h EMAs)', 'ema'],
  trend_h: ['Trend (1h EMAs)', 'ema'],
  momentum: ['Momentum (RSI/MACD)', 'rsi'],
  sentiment: ['News & social tone', 'mindshare'],
  positioning: ['Positioning (contrarian)', 'funding'],
}
// which forecast horizon to draw as a cone for a given chart interval
const CONE_H: Record<string, number> = { '1m': 4, '5m': 4, '15m': 4, '30m': 24, '1h': 24, '4h': 168, '1d': 168 }

function ScoreCard({ sig }: { sig: any }) {
  if (!sig) return <Panel title="Rook score"><Skel n={6} /></Panel>
  const t1 = sig.t1h || {}
  const td = sig.t1d || {}
  return (
    <Panel title={<Term k="score">Rook score</Term>} right={<ScoreChip score={sig.score} label={sig.label} />}>
      <div className="h2" style={{ marginBottom: '0.625rem' }}>{sig.label}</div>
      <div className="col" style={{ gap: '0.4375rem' }}>
        {Object.entries(sig.components || {}).map(([k, v]) => (
          <div key={k} className="row small">
            <span className="muted" style={{ width: '11rem' }}>{COMP_LABEL[k] ? <Term k={COMP_LABEL[k][1]}>{COMP_LABEL[k][0]}</Term> : k}</span>
            <div className="grow"><CBar v={v as number} /></div>
            <span className={`num ${tone(v)}`} style={{ width: '2.875rem', textAlign: 'right' }}>{(v as number) > 0 ? '+' : ''}{(v as number).toFixed(2)}</span>
          </div>
        ))}
      </div>
      {sig.notes?.length > 0 && (
        <ul className="small" style={{ margin: '0.75rem 0 0', paddingLeft: '1rem', color: 'var(--ink-2)' }}>
          {sig.notes.map((n: string) => <li key={n} style={{ marginBottom: '0.1875rem' }}>{n}</li>)}
        </ul>
      )}
      <hr className="sep" />
      <div className="kv">
        <span><Term k="rsi">RSI</Term> 1h / 1d</span><span>{num(t1.rsi, 0)} / {num(td.rsi, 0)}</span>
        <span><Term k="ema">EMA stack</Term> 1h / 4h / 1d</span><span>{t1.ema_stack || '—'} / {sig.t4h?.ema_stack || '—'} / {td.ema_stack || '—'}</span>
        <span><Term k="atr">ATR</Term> 1h</span><span>{num(t1.atr_pct, 2)}%</span>
        <span>1d EMA50 / EMA200</span><span>{price(td.ema50)} / {price(td.ema200)}</span>
        <span>50-day range</span><span>{price(td.swing_lo)} – {price(td.swing_hi)}</span>
        <span><Term k="bollinger">Bollinger</Term> 1d</span><span>{price(td.bb_lo)} – {price(td.bb_hi)}</span>
      </div>
    </Panel>
  )
}

function Forecast({ rows }: { rows: any[] }) {
  if (!rows.length) return null
  return (
    <Panel title="Model forecast" sub="calibrated · shrunk to proven skill" right={<a className="btn ghost sm" href="#/predictions">details</a>}>
      {rows.map((p) => (
        <div key={p.h} style={{ marginBottom: '0.75rem' }}>
          <div className="row small">
            <b className="num" style={{ width: '2rem' }}>{p.h >= 168 ? '7d' : `${p.h}h`}</b>
            <span className={`num ${p.p_up > 0.5 ? 'up' : p.p_up < 0.5 ? 'down' : ''}`}><Term k="pup">P(up)</Term> {(p.p_up * 100).toFixed(1)}%</span>
            <span className="faint">base {(p.p_base * 100).toFixed(0)}%</span>
            <span className="grow" />
            <EdgeBadge edge={p.edge} />
          </div>
          <div className="row tiny muted" style={{ marginTop: '0.1875rem' }}>
            <span><Term k="cone">10–90% cone</Term> {price(p.range80?.[0])} – {price(p.range80?.[1])}</span>
            <span className="grow" />
            <span>resolves in {until(p.resolve_at)}</span>
          </div>
          {p.coverage?.n > 0 && (
            <div className="tiny muted" style={{ marginTop: '0.125rem' }}>
              <Term k="coverage">cone hit-rate</Term> {(p.coverage.rate * 100).toFixed(0)}% of {p.coverage.n} graded (target 80%) · width ×{num(p.width, 2)}
            </div>
          )}
        </div>
      ))}
    </Panel>
  )
}

function Positioning({ sym }: { sym: string }) {
  const { data, error } = useApi<any>(`/api/crypto/positioning?sym=${sym}`, 120000)
  if (error) return null
  const last = (k: string) => data?.[k]?.at(-1)?.v
  const fund = data?.funding || []
  return (
    <Panel title="Positioning" sub="perps · Binance futures data">
      {!data && <Skel n={5} />}
      {data && (
        <>
          <div className="kv">
            <span><Term k="oi">Open interest</Term></span><span>${big(last('oi'))}</span>
            <span>OI 24h</span><span><Delta v={data.oi_chg24} /></span>
            <span><Term k="ls">Retail long/short</Term></span><span>{num(last('ls_retail'), 2)}</span>
            <span>Top traders L/S (pos.)</span><span>{num(last('ls_top'), 2)}</span>
            <span><Term k="taker">Taker buy/sell</Term></span><span className={tone((last('taker') ?? 1) - 1)}>{num(last('taker'), 2)}</span>
            <span><Term k="funding">Funding</Term> (last)</span><span>{isNum(fund.at(-1)?.v) ? `${fund.at(-1).v.toFixed(4)}%` : '—'}</span>
          </div>
          <div className="row" style={{ marginTop: '0.625rem', gap: '0.875rem' }}>
            <div>
              <div className="tiny faint">OI 48h</div>
              <Spark data={(data.oi || []).map((x: any) => x.v)} w={140} h={30} color="var(--ice)" />
            </div>
            <div>
              <div className="tiny faint">funding 30d</div>
              <Spark data={fund.map((x: any) => x.v)} w={140} h={30} color="var(--accent)" />
            </div>
          </div>
        </>
      )}
    </Panel>
  )
}

function CryptoFund({ sym }: { sym: string }) {
  const { data, error } = useApi<any>(`/api/crypto/coin?sym=${sym}`)
  const [more, setMore] = useState(false)
  const d = data
  const p = d?.defi?.protocol
  return (
    <Panel title="Fundamentals" sub={d?.source || ''}>
      <Err e={error || d?.error} />
      {!d && <Skel n={8} />}
      {d && (
        <>
          <div className="row wrap" style={{ marginBottom: '0.625rem' }}>
            {(d.categories || []).slice(0, 6).map((c: string) => <span key={c} className="chip">{c}</span>)}
          </div>
          <div className="kv">
            <span>Market cap</span><span>${big(d.mcap)}</span>
            <span><Term k="fdv">FDV</Term></span><span>${big(d.fdv)}</span>
            <span>Mcap / FDV</span><span className={isNum(d.mcap_fdv) && d.mcap_fdv < 0.5 ? 'warn' : ''}>{isNum(d.mcap_fdv) ? `${(d.mcap_fdv * 100).toFixed(0)}%` : '—'}</span>
            <span>Supply issued</span><span>{isNum(d.supply_issued_pct) ? `${d.supply_issued_pct.toFixed(1)}% of max` : 'no max'}</span>
            <span>24h volume</span><span>${big(d.vol)}</span>
            <span>ATH</span><span>{price(d.ath)} <span className="down small">{isNum(d.ath) && isNum(d.price) ? pct((d.price / d.ath - 1) * 100, 0) : ''}</span></span>
            <span>30d / 1y</span><span><Delta v={d.chg30d} d={1} /> / <Delta v={d.chg1y} d={1} /></span>
            {d.dev?.commit_count_4_weeks != null && (<><span>Commits (4w)</span><span>{d.dev.commit_count_4_weeks}</span></>)}
            {d.sentiment_up != null && (<><span>CG community bullish</span><span>{num(d.sentiment_up, 0)}%</span></>)}
          </div>
          {d.defi?.chain && (
            <>
              <hr className="sep" />
              <div className="kv"><span>Chain <Term k="tvl">TVL</Term> ({d.defi.chain.name})</span><span>${big(d.defi.chain.tvl)}</span></div>
            </>
          )}
          {p && (
            <>
              <hr className="sep" />
              <div className="label" style={{ marginBottom: '0.375rem' }}>DeFi · {p.name} ({p.category})</div>
              <div className="kv">
                <span>TVL</span><span>${big(p.tvl)} <Delta v={p.chg7d} d={1} className="small" /> 7d</span>
                <span>Fees 30d</span><span>${big(p.fees30d)}</span>
                <span>Revenue 30d</span><span>${big(p.rev30d)}</span>
                <span>P/E (annualised rev.)</span><span>{num(p.pe_annualized, 1)}</span>
              </div>
            </>
          )}
          {d.description && (
            <button className="linkish small muted" style={{ marginTop: '0.75rem', textAlign: 'left' }} onClick={() => setMore(!more)} aria-expanded={more}>
              {more ? d.description : `${d.description.slice(0, 220)}…`}
            </button>
          )}
        </>
      )}
    </Panel>
  )
}

function DexFund({ t }: { t: any }) {
  if (!t) return <Panel title="Token"><Skel n={8} /></Panel>
  return (
    <Panel title="On-chain token" sub="Jupiter · GeckoTerminal">
      <div className="row wrap" style={{ marginBottom: '0.625rem' }}>
        <span className={`flag ${t.verified ? 'ok' : 'unverified'}`}><Term k="verified">{t.verified ? 'verified' : 'unverified'}</Term></span>
        {t.organic && <span className="chip">organic score: {t.organic}</span>}
        {(t.tags || []).slice(0, 4).map((x: string) => <span key={x} className="chip">{x}</span>)}
      </div>
      <div className="kv">
        <span>Price</span><span>${price(t.price)}</span>
        <span>Liquidity</span><span className={(t.liquidity || 0) < 250000 ? 'warn' : ''}>${big(t.liquidity)}</span>
        <span>Market cap</span><span>${big(t.mcap)}</span>
        <span><Term k="fdv">FDV</Term></span><span>${big(t.fdv)}</span>
        <span>Holders</span><span>{isNum(t.holders) ? t.holders.toLocaleString() : '—'}</span>
        <span>24h volume</span><span>${big(t.vol24)}</span>
        <span>Deepest pool</span><span>{t.pool?.name || '—'} {t.pool?.liquidity ? `($${big(t.pool.liquidity)})` : ''}</span>
        <span>Mint</span><span className="addr" title={t.mint}>{t.mint?.slice(0, 6)}…{t.mint?.slice(-6)}</span>
      </div>
      <div className="tiny faint" style={{ marginTop: '0.625rem' }}>On-chain-only tokens have no derivatives data and thin liquidity: treat every signal here as high risk.</div>
    </Panel>
  )
}

function StockFund({ sym, last }: { sym: string; last?: number }) {
  const { data: d, error } = useApi<any>(`/api/stocks/fundamentals?sym=${encodeURIComponent(sym)}`)
  const [more, setMore] = useState(false)
  const pc = (x: unknown) => (isNum(x) ? `${(x * 100).toFixed(1)}%` : '—')
  const up = isNum(d?.target_mean) && isNum(last) ? (d.target_mean / last - 1) * 100 : null
  return (
    <Panel title="Fundamentals" sub={d?.source || ''}>
      <Err e={error} />
      {!d && !error && <Skel n={10} />}
      {d && (
        <>
          <div className="row wrap" style={{ marginBottom: '0.625rem' }}>
            {d.sector && <span className="chip">{d.sector}</span>}
            {d.industry && <span className="chip">{d.industry}</span>}
            {d.next_earnings && <span className="chip accent">earnings {d.next_earnings}</span>}
          </div>
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: '1.125rem' }}>
            <div className="kv">
              <span>Market cap</span><span>{big(d.mcap)}</span>
              <span>P/E · fwd</span><span>{num(d.pe, 1)} · {num(d.fpe, 1)}</span>
              <span>P/S · P/B</span><span>{num(d.ps, 1)} · {num(d.pb, 1)}</span>
              <span>PEG</span><span>{num(d.peg, 2)}</span>
              <span>EV/EBITDA</span><span>{num(d.ev_ebitda, 1)}</span>
              <span>Dividend yield</span><span>{pc(d.div_yield)}</span>
              <span>Beta</span><span>{num(d.beta, 2)}</span>
            </div>
            <div className="kv">
              <span>Revenue (TTM)</span><span>{big(d.revenue)}</span>
              <span>Rev. growth</span><span className={tone(d.rev_growth)}>{pc(d.rev_growth)}</span>
              <span>Gross / op. margin</span><span>{pc(d.gross_margin)} / {pc(d.op_margin)}</span>
              <span>Net margin</span><span>{pc(d.net_margin)}</span>
              <span>ROE</span><span>{pc(d.roe)}</span>
              <span>FCF</span><span>{big(d.fcf)}</span>
              <span>Cash / debt</span><span>{big(d.cash)} / {big(d.debt)}</span>
            </div>
          </div>
          {d.target_mean && (
            <>
              <hr className="sep" />
              <div className="row small wrap">
                <span className="label">Analysts</span>
                <span className="chip">{d.rec?.replace('_', ' ') || '—'}</span>
                <span className="muted">{d.analysts} analysts · target {price(d.target_lo)} / <b>{price(d.target_mean)}</b> / {price(d.target_hi)}</span>
                <span className="grow" />
                {isNum(up) && <Delta v={up} d={1} />}
              </div>
            </>
          )}
          {d.summary && (
            <button className="linkish small muted" style={{ marginTop: '0.75rem', textAlign: 'left' }} onClick={() => setMore(!more)} aria-expanded={more}>
              {more ? d.summary : `${d.summary.slice(0, 240)}…`}
            </button>
          )}
        </>
      )}
    </Panel>
  )
}

function SymNews({ sym }: { sym: string }) {
  const { data } = useApi<any>(sym ? `/api/sentiment/symbol?sym=${encodeURIComponent(sym)}&days=14` : null, 300000)
  const hist = data?.hist || []
  return (
    <Panel title="News & sentiment" sub={hist.length ? `${hist.length}h of history` : ''} flush>
      {hist.length > 2 && (
        <div className="row" style={{ padding: '0.5rem 0.75rem', gap: '1.125rem', borderBottom: '1px solid var(--line)' }}>
          <div><div className="tiny faint">tone</div><Spark data={hist.map((h: any) => h.sentiment)} w={150} h={28} color="var(--ice)" /></div>
          <div><div className="tiny faint">mentions</div><Spark data={hist.map((h: any) => h.mentions)} w={150} h={28} color="var(--accent)" /></div>
        </div>
      )}
      <div className="scroll" style={{ maxHeight: '28rem' }}>
        <NewsList items={data?.news || []} />
        {data && !data.news.length && <div className="empty">No recent headlines mention {sym}.</div>}
      </div>
    </Panel>
  )
}

/** Elfa: X attention for the ticker, most-engaged posts (1 credit, cached 3h) and an on-demand chatter summary (5). */
function XPanel({ sym, name }: { sym: string; name?: string }) {
  const { secrets } = useSettings()
  const configured = !!secrets?.ELFA_API_KEY?.set
  const { data: x } = useApi<any>(sym && configured ? `/api/elfa/x?sym=${encodeURIComponent(sym)}` : null, 600000)
  const [m, setM] = useState<any>(null)
  const [ev, setEv] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState('')
  useEffect(() => {
    setM(null)
    setEv(null)
    setErr(null)
    if (sym && configured) api(`/api/elfa/mentions?sym=${encodeURIComponent(sym)}&fetch=false`).then(setM).catch(() => undefined)
  }, [sym, configured])
  const loadMentions = async () => {
    setBusy('m')
    setErr(null)
    try {
      setM(await api(`/api/elfa/mentions?sym=${encodeURIComponent(sym)}&fetch=true`))
    } catch (e) {
      setErr((e as Error).message)
    }
    setBusy('')
  }
  const summarize = async () => {
    setBusy('e')
    setErr(null)
    try {
      setEv(await post('/api/elfa/events', { keywords: [sym.toLowerCase(), ...(name ? [name.toLowerCase()] : [])] }))
    } catch (e) {
      setErr((e as Error).message)
    }
    setBusy('')
  }
  if (!configured)
    return (
      <Panel title={<Term k="xmentions">X / social (Elfa)</Term>}>
        <div className="muted small">Add an Elfa key in Settings → API keys to see X attention, top posts and chatter summaries.</div>
      </Panel>
    )
  const hist = x?.hist || []
  const fresh = m && m.ts && Date.now() / 1000 - m.ts < 3 * 3600
  return (
    <Panel title={<Term k="xmentions">X / social (Elfa)</Term>} sub={x?.ts ? `snapshot ${ago(x.ts)} ago` : ''} flush>
      <div style={{ padding: '0.625rem 0.75rem', borderBottom: '1px solid var(--line)' }}>
        {x?.listed ? (
          <div className="row wrap" style={{ gap: '1rem' }}>
            <div><div className="tiny faint">mentions 24h</div><b className="num">{x.mentions?.toLocaleString()}</b></div>
            <div><div className="tiny faint">vs prior 24h</div><Delta v={x.chg} d={0} /></div>
            <div><div className="tiny faint">rank</div><b className="num">#{x.rank}</b></div>
            {hist.length > 2 && <div><div className="tiny faint">7d trend</div><Spark data={hist.map((h: any) => h.cur)} w={120} h={26} color="var(--ice)" /></div>}
          </div>
        ) : (
          <div className="muted small">{x ? `${sym} is not in Elfa's trending list right now (fewer than ~5 mentions, or outside the top 60).` : 'No Elfa snapshot yet: the first refresh runs a few minutes after start.'}</div>
        )}
      </div>
      <Err e={err} />
      <div className="row" style={{ padding: '0.5rem 0.75rem', gap: '0.5rem' }}>
        <span className="label">Most-engaged posts (24h)</span>
        <span className="grow" />
        {!fresh && (
          <button className="btn sm" onClick={loadMentions} disabled={busy === 'm'} title="Costs 1 Elfa credit, cached 3h">
            {busy === 'm' ? 'Loading…' : m?.rows?.length ? 'Refresh · 1 credit' : 'Load · 1 credit'}
          </button>
        )}
      </div>
      <div className="scroll" style={{ maxHeight: '16rem' }}>
        {(m?.rows || []).slice(0, 8).map((r: any) => (
          <a key={r.link} className="mention" href={r.link} target="_blank" rel="noreferrer">
            <span className="small"><b>@{r.user || 'unknown'}</b> <span className="faint">{r.type}</span></span>
            <span className="tiny muted num">{big(r.views, 1)} views</span>
            <span className="tiny muted">
              {r.likes.toLocaleString()} likes · {r.reposts} reposts{r.smart_reposts ? ` · ${r.smart_reposts} smart` : ''}{r.ct_reposts ? ` · ${r.ct_reposts} CT` : ''}
            </span>
            <span className="tiny faint">{r.ts ? ago(Date.parse(r.ts) / 1000) : ''} <ExternalLink size={11} aria-hidden="true" /></span>
          </a>
        ))}
        {m && !m.rows?.length && <div className="empty small">No posts loaded yet.</div>}
      </div>
      <div style={{ padding: '0.625rem 0.75rem', borderTop: '1px solid var(--line)' }}>
        {!ev && (
          <button className="btn sm" onClick={summarize} disabled={busy === 'e'} title="Costs 5 Elfa credits, cached 6h">
            <Sparkles size={13} aria-hidden="true" /> {busy === 'e' ? 'Summarizing…' : 'What is X saying? · 5 credits'}
          </button>
        )}
        {ev && (
          <div className="col" style={{ gap: '0.5rem' }}>
            {(ev.rows || []).map((r: any, i: number) => (
              <div key={i} className="small">
                {r.text} {(r.links || []).map((l: string, j: number) => <a key={l} className="ice tiny" href={l} target="_blank" rel="noreferrer"> [{j + 1}]</a>)}
              </div>
            ))}
            {!ev.rows?.length && <div className="muted small">Elfa found no notable events for {sym} in 24h.</div>}
          </div>
        )}
      </div>
    </Panel>
  )
}

export default function Asset({ kind, sym }: { kind: Kind; sym: string }) {
  const S = kind === 'crypto' ? sym.toUpperCase() : sym
  const defIv = kind === 'stock' ? '1d' : '1h'
  const [iv, setIv] = useState(defIv)
  const [info, setInfo] = useState<ChartInfo>({})
  const [ind, setInd] = useState<Ind>({ ema9: true, ema21: true, ema50: true, ema200: true, vol: true, rsi: true, macd: false })
  const [showCone, setShowCone] = useState(true)
  const [showLv, setShowLv] = useState(true)
  const { settings } = useSettings()
  const tick = useLive(kind === 'stock' ? null : S)
  const { data: sig, error: sigErr } = useApi<any>(`/api/signals/one?sym=${encodeURIComponent(S)}&kind=${kind}`, 120000)
  const { data: q } = useApi<any[]>(kind === 'stock' ? `/api/stocks/quotes?syms=${encodeURIComponent(S)}` : null, 30000)
  const { data: tok } = useApi<any>(kind === 'dex' ? `/api/dex/token?mint=${encodeURIComponent(S)}` : null, 60000)
  const fc = useApi<any[]>('/api/predictions/current', 300000).data
  useEffect(() => setInfo({}), [S, iv])
  useEffect(() => setIv(defIv), [kind, defIv])

  const sq = q?.[0]
  const last = kind === 'stock' ? sq?.price ?? info.last : tick?.price ?? info.last
  const chg = kind === 'stock' ? sq?.chg : tick?.chg24 ?? (kind === 'dex' ? tok?.chg24 : undefined)
  const title = kind === 'dex' ? tok?.symbol || sig?.sym || `${S.slice(0, 6)}…` : S.replace('.SA', '')
  const wlKey = kind === 'crypto' ? 'crypto' : kind === 'dex' ? 'dex' : 'stocks'
  const inWl = kind === 'dex' ? (settings?.watchlist?.dex || []).some((d: any) => d.mint === S) : (settings?.watchlist?.[wlKey] || []).includes(S)
  const preds = (fc || []).filter((p) => p.sym === S)
  const cp = preds.find((p) => p.h === CONE_H[iv]) || null
  const cone: Cone | null = showCone && cp?.cone && kind === 'crypto' ? { t0: cp.issued, price: cp.price, h: cp.h, q: cp.cone } : null
  const lvNear = sig?.levels?.near
  const lines: PriceLine[] = showLv && lvNear
    ? [
        ...(lvNear.support || []).slice(0, 2).map((e: any) => ({ price: e.price, title: `S ${e.touches}×`, color: 'rgba(47,191,136,.55)' })),
        ...(lvNear.resistance || []).slice(0, 2).map((e: any) => ({ price: e.price, title: `R ${e.touches}×`, color: 'rgba(242,86,107,.55)' })),
      ]
    : []
  const addWl = async () => {
    await post('/api/watchlist', kind === 'dex' ? { kind: 'dex', mint: S, sym: tok?.symbol, name: tok?.name } : { kind, sym: S })
    await loadSettings()
  }
  const xSym = kind === 'dex' ? (tok?.symbol || '').toUpperCase() : S

  return (
    <div className="col">
      <div className="page-head">
        <div>
          <div className="row wrap">
            {kind === 'dex' && tok?.icon && <img className="tok-icon" src={tok.icon} alt="" />}
            <h1 className="h1" style={{ margin: 0 }}>{title}</h1>
            {(sq?.name || tok?.name) && <span className="muted">{sq?.name || tok?.name}</span>}
            <span className="chip">{kind === 'dex' ? 'on-chain' : kind}</span>
            {sig && <ScoreChip score={sig.score} label={sig.label} />}
          </div>
          <div className="row wrap" style={{ marginTop: '0.25rem' }}>
            <Flash v={last} className="num"><span style={{ fontSize: '1.5rem', fontWeight: 500 }}>{price(last)}</span></Flash>
            <Delta v={chg} />
            <span className="tiny muted">
              {info.feed ? `feed: ${info.feed}` : ''}
              {isNum(info.latency) ? <> · <Term k="latency">{info.latency}ms</Term></> : ''}
              {info.source ? ` · history: ${info.source}` : ''}
              {sq?.state ? ` · ${sq.state.toLowerCase()}` : ''}
            </span>
          </div>
        </div>
        <span className="grow" />
        {!inWl && <button className="btn" onClick={addWl}><Plus aria-hidden="true" /> Watchlist</button>}
        {kind !== 'dex' && <button className="btn" onClick={() => go(`alerts?sym=${encodeURIComponent(S)}&kind=${kind}`)}><Bell aria-hidden="true" /> Alert</button>}
        <button className="btn primary" onClick={() => go(`ask?q=${encodeURIComponent(`Full analysis of ${title}: trend, levels, positioning, fundamentals, sentiment, and a trade plan with invalidation.`)}&focus=${encodeURIComponent(kind === 'dex' ? title : S)}`)}>
          <MessageSquare aria-hidden="true" /> Ask Rook
        </button>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1fr) 25rem' }}>
        <Panel
          flush
          title={<Seg label="Chart interval" value={iv} options={kind === 'dex' ? DEX_IVS : IVS} onChange={setIv} />}
          right={
            <div className="seg" role="group" aria-label="Chart overlays">
              {(['ema9', 'ema21', 'ema50', 'ema200', 'vol', 'rsi', 'macd'] as (keyof Ind)[]).map((k) => (
                <button key={k} className={ind[k] ? 'on' : ''} aria-pressed={!!ind[k]} onClick={() => setInd((x) => ({ ...x, [k]: !x[k] }))}>
                  {k.toUpperCase()}
                </button>
              ))}
              <button className={showLv ? 'on' : ''} aria-pressed={showLv} onClick={() => setShowLv(!showLv)} title="Support / resistance">LEVELS</button>
              {kind === 'crypto' && preds.length > 0 && (
                <button className={showCone ? 'on' : ''} aria-pressed={showCone} onClick={() => setShowCone(!showCone)} title="Forecast cone (10-90%)">CONE</button>
              )}
            </div>
          }
        >
          <div style={{ height: 'calc(100vh - 16rem)', minHeight: '30rem' }}>
            <Chart sym={S} kind={kind} interval={iv} ind={ind} priceLines={lines} cone={cone} onInfo={(i) => setInfo((x) => ({ ...x, ...i }))} />
          </div>
          {cone && cp && (
            <div className="tiny muted" style={{ padding: '0.375rem 0.75rem', borderTop: '1px solid var(--line)' }}>
              Dashed blue: <Term k="cone">{cp.h >= 168 ? '7-day' : `${cp.h}h`} forecast cone</Term> (10–90%), orange dotted: median.
              {cp.coverage?.n > 0 ? ` Realised hit-rate so far ${(cp.coverage.rate * 100).toFixed(0)}% of ${cp.coverage.n}.` : ' Its hit-rate is graded as forecasts mature.'}
            </div>
          )}
        </Panel>
        <div className="col">
          <Panel title="What this means" sub="plain-language readout">
            <Err e={sigErr} />
            <Readout ro={sig?.readout} score={sig?.score} />
          </Panel>
          {kind === 'crypto' && <Forecast rows={preds} />}
          <Panel title={<Term k="setup">Active setups</Term>} sub="with their own track record">
            <SetupList items={sig?.setups} />
          </Panel>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(26rem, 1fr))', alignItems: 'start' }}>
        <Panel title={<Term k="levels">Support & resistance</Term>} sub="4h pivots · daily below">
          <Levels lv={sig?.levels?.near} last={last} />
          {sig?.levels?.major && (sig.levels.major.support?.length > 0 || sig.levels.major.resistance?.length > 0) && (
            <>
              <hr className="sep" />
              <div className="label" style={{ marginBottom: '0.375rem' }}>Daily (major)</div>
              <Levels lv={sig.levels.major} last={last} />
            </>
          )}
        </Panel>
        <ScoreCard sig={sig} />
        {kind === 'crypto' && sig?.deriv && <Positioning sym={S} />}
        {kind === 'dex' && <DexFund t={tok ? { ...tok, mint: S } : null} />}
        {kind !== 'stock' && <XPanel sym={xSym} name={kind === 'dex' ? tok?.name : undefined} />}
        {kind === 'crypto' && <CryptoFund sym={S} />}
        {kind === 'stock' && <StockFund sym={S} last={last} />}
        {kind === 'crypto' && (
          <Panel title="Order book & trades" sub="direct exchange stream" flush>
            <OrderBook sym={S} />
          </Panel>
        )}
        <SymNews sym={xSym} />
      </div>
    </div>
  )
}
