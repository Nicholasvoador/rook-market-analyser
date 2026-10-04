import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, RefreshCw, Sparkles } from 'lucide-react'
import Chart, { type ChartInfo, type Cone } from '../components/Chart'
import Markdown from '../components/Markdown'
import { CBar, Delta, EdgeBadge, Err, Flash, Gauge, Panel, ScoreChip, Seg, SentChip, Skel, Spark, SymIcon, Term } from '../components/ui'
import { post, useApi } from '../lib/api'
import { ago, big, clock, isNum, money, num, pct, price, tone } from '../lib/fmt'
import { hub } from '../lib/hub'
import { assetHref, go, useLive, useSettings } from '../lib/store'

// ------------------------------------------------------------------ regime strip
function Regime() {
  const { data: g } = useApi<any>('/api/crypto/global', 60000)
  const { data: s } = useApi<any>('/api/sentiment/overview', 120000)
  const { data: m } = useApi<any>('/api/macro', 60000)
  const { data: oc } = useApi<any>('/api/crypto/onchain', 300000)
  const { data: btc } = useApi<any>('/api/signals/one?sym=BTC', 120000)
  const liq = useLiq()
  const { settings, rate } = useSettings()
  const ccy = settings?.display?.currency || 'USD'

  const dxy = m?.quotes?.find((q: any) => q.label === 'DXY')
  const vix = m?.quotes?.find((q: any) => q.label === 'VIX')
  const regime = useMemo(() => {
    if (!btc || !s?.fng) return null
    let x = 0.45 * (btc.score / 100) + 0.2 * ((s.fng.value - 50) / 50)
    if (isNum(oc?.stables_chg7d)) x += 0.15 * Math.sign(oc.stables_chg7d)
    if (isNum(dxy?.chg)) x += 0.1 * -Math.sign(dxy.chg)
    if (isNum(vix?.price)) x += 0.1 * (vix.price < 18 ? 1 : vix.price > 25 ? -1 : 0)
    return { x, label: x > 0.2 ? 'Risk-on' : x < -0.2 ? 'Risk-off' : 'Neutral' }
  }, [btc, s, oc, dxy, vix])

  const dv = oc?.dvol?.BTC
  return (
    <section className="panel">
      <div className="strip">
        <div className="stat">
          <span className="label">Regime</span>
          <span className={`v ${regime ? (regime.x > 0.2 ? 'up' : regime.x < -0.2 ? 'down' : '') : ''}`} title="BTC trend score, crypto F&G, stablecoin growth, DXY and VIX">
            {regime?.label ?? '—'}
          </span>
          <span className="d muted">composite {regime ? (regime.x > 0 ? '+' : '') + regime.x.toFixed(2) : '—'}</span>
        </div>
        <div className="stat">
          <span className="label">Crypto mkt cap</span>
          <span className="v">{money(g?.mcap, ccy, rate, true)}</span>
          <Delta v={g?.mcap_chg24} className="d" />
        </div>
        <div className="stat">
          <span className="label">BTC dominance</span>
          <span className="v">{num(g?.btc_dom, 1)}%</span>
          <span className="d muted">ETH {num(g?.eth_dom, 1)}%</span>
        </div>
        <div className="stat" style={{ alignItems: 'center', paddingBottom: '0.25rem' }}>
          <span className="label">Crypto F&G</span>
          <Gauge v={s?.fng?.value} label={s?.fng?.label} size={84} />
        </div>
        <div className="stat" style={{ alignItems: 'center', paddingBottom: '0.25rem' }}>
          <span className="label">Stocks F&G</span>
          <Gauge v={m?.cnn_fg?.value} label={m?.cnn_fg?.label} size={84} />
        </div>
        <div className="stat">
          <span className="label">BTC DVOL</span>
          <span className="v">{num(dv?.now, 1)}</span>
          <span className={`d ${dv && dv.d24 ? tone(dv.now - dv.d24) : ''}`}>
            {dv?.d24 ? `${dv.now - dv.d24 > 0 ? '+' : ''}${(dv.now - dv.d24).toFixed(1)} 24h` : '—'}
          </span>
        </div>
        <div className="stat">
          <span className="label">Stablecoins</span>
          <span className="v">{money(oc?.stables?.at(-1)?.v, ccy, rate, true)}</span>
          <span className="d">
            <Delta v={oc?.stables_chg7d} /> <span className="muted">7d</span>
          </span>
        </div>
        <div className="stat">
          <span className="label">Liqs 1h L / S</span>
          <span className="v">
            <span className="down">{big(liq.total.long, 1)}</span>
            <span className="faint"> / </span>
            <span className="up">{big(liq.total.short, 1)}</span>
          </span>
          <span className="d muted">{liq.source || '—'}</span>
        </div>
        <div className="stat">
          <span className="label">USD/BRL</span>
          <span className="v">{num(m?.usdbrl?.rate, 4)}</span>
          <Delta v={m?.usdbrl?.chg} className="d" />
        </div>
      </div>
    </section>
  )
}

// ------------------------------------------------------------------ liquidations (live)
type Liq = { t: number; sym: string; side: 'long' | 'short'; usd: number; price: number; venue: string }
function useLiq() {
  const { data } = useApi<any>('/api/crypto/liquidations', 60000)
  const [evs, setEvs] = useState<Liq[]>([])
  useEffect(() => {
    if (data?.recent) setEvs(data.recent)
  }, [data])
  useEffect(
    () =>
      hub.on('liq', (ev) => {
        setEvs((x) => [...(ev.events as Liq[]).reverse(), ...x].slice(0, 300))
      }),
    [],
  )
  const total = useMemo(() => {
    const now = Date.now() / 1000
    const t = { long: 0, short: 0 }
    for (const e of evs) if (now - e.t < 3600) t[e.side] += e.usd
    return t
  }, [evs])
  return { evs, total, source: data?.source as string | undefined }
}

function Liquidations() {
  const { evs, total } = useLiq()
  const tot = total.long + total.short || 1
  return (
    <Panel title="Liquidations" sub="live · perps" flush>
      <div style={{ padding: '0.625rem 0.75rem' }}>
        <div className="row small" style={{ justifyContent: 'space-between', marginBottom: '0.3125rem' }}>
          <span className="down">Longs ${big(total.long, 1)}</span>
          <span className="up">Shorts ${big(total.short, 1)}</span>
        </div>
        <div className="bar" style={{ height: '0.375rem', background: 'var(--up)' }}>
          <i style={{ width: `${(total.long / tot) * 100}%`, background: 'var(--down)' }} />
        </div>
      </div>
      <div className="scroll" style={{ maxHeight: '18.125rem' }}>
        <table className="t">
          <tbody>
            {evs.slice(0, 60).map((e, i) => (
              <tr key={`${e.t}-${i}`} style={{ opacity: e.usd < 5000 ? 0.55 : 1 }}>
                <td className="faint num">{clock(e.t)}</td>
                <td>
                  <a href={assetHref(e.sym, 'crypto')}>{e.sym}</a>
                </td>
                <td className={e.side === 'long' ? 'down' : 'up'}>{e.side}</td>
                <td className="num" style={{ fontWeight: e.usd > 100000 ? 600 : 400 }}>
                  ${big(e.usd, 1)}
                </td>
              </tr>
            ))}
            {!evs.length && (
              <tr>
                <td className="empty">Waiting for liquidations…</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

// ------------------------------------------------------------------ predictions
function Forecasts() {
  const { data, setData } = useApi<any[]>('/api/predictions/current', 120000)
  useEffect(() => hub.on('predictions', (ev) => setData(ev.current)), [setData])
  const by = useMemo(() => {
    const m = new Map<string, any[]>()
    for (const p of data || []) m.set(p.sym, [...(m.get(p.sym) || []), p])
    return [...m.entries()]
  }, [data])
  return (
    <Panel
      title="Model forecasts"
      sub="P(price higher) · self-graded"
      right={
        <a className="btn ghost sm" href="#/predictions">
          track record <ArrowUpRight size={12} />
        </a>
      }
    >
      {!data && <Skel n={6} />}
      {data && !data.length && <div className="empty">Models are training: first forecasts appear within the hour.</div>}
      <div className="col" style={{ gap: '0.875rem' }}>
        {by.map(([sym, rows]) => (
          <div key={sym}>
            <div className="row" style={{ marginBottom: '0.375rem' }}>
              <a className="h2" href={assetHref(sym, 'crypto')}>
                {sym}
              </a>
              <span className="muted small num">@ {price(rows[0].price)}</span>
            </div>
            {rows.map((p: any) => (
              <div key={p.h} className="row small" style={{ marginBottom: '0.3125rem' }}>
                <span className="num muted" style={{ width: '2.125rem' }}>
                  {p.h >= 168 ? '7d' : `${p.h}h`}
                </span>
                <span className={`num ${p.p_up > 0.5 ? 'up' : p.p_up < 0.5 ? 'down' : ''}`} style={{ width: '2.375rem' }}>
                  {(p.p_up * 100).toFixed(0)}%
                </span>
                <div className="grow">
                  <CBar v={p.p_up - 0.5} max={0.25} />
                </div>
                <EdgeBadge edge={p.edge} />
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="tiny faint" style={{ marginTop: '0.625rem' }}>
        Forecasts shrink toward the base rate unless the model has proven skill. "No edge" means: don't trade on this.
      </div>
    </Panel>
  )
}

// ------------------------------------------------------------------ watchlist
function WatchRow({ t, mk, sig, fund, ccy, rate }: { t: any; mk: any; sig: any; fund: any; ccy: string; rate: number }) {
  const l = useLive(t.sym)
  const p = l?.price ?? t.last
  const c = l?.chg24 ?? t.chg24
  return (
    <tr className="click" onClick={() => (location.hash = assetHref(t.sym, 'crypto'))}>
      <td>
        <div className="sym" title={`streaming from ${t.venue}`}>
          <SymIcon sym={t.sym} image={mk?.image} />
          {t.sym}
        </div>
      </td>
      <td className="num">
        <Flash v={p}>{ccy === 'BRL' ? money(p, ccy, rate) : price(p)}</Flash>
      </td>
      <td className="num">
        <Delta v={mk?.chg1h} />
      </td>
      <td className="num">
        <Delta v={c} />
      </td>
      <td className="num">
        <Delta v={mk?.chg7d} />
      </td>
      <td>
        <Spark data={mk?.spark || []} w={80} h={22} />
      </td>
      <td className="num">{big(mk?.mcap)}</td>
      <td className="num">
        <span className={fund ? (fund.fund8h > 0.02 ? 'accent' : fund.fund8h < -0.005 ? 'ice' : '') : ''}>{fund ? `${fund.fund8h.toFixed(4)}%` : '—'}</span>
      </td>
      <td className="num">{sig?.t1h?.rsi ? sig.t1h.rsi.toFixed(0) : '—'}</td>
      <td>
        <span className={`chip ${sig?.t1d?.ema_stack === 'bull' ? 'up' : sig?.t1d?.ema_stack === 'bear' ? 'down' : ''}`}>{sig?.t1d?.ema_stack || '—'}</span>
      </td>
      <td>
        <SentChip v={sig?.sentiment?.sentiment} n={sig?.sentiment?.mentions} />
      </td>
      <td>{sig ? <ScoreChip score={sig.score} label={sig.label} /> : '—'}</td>
    </tr>
  )
}

function Watchlist() {
  const { settings, rate } = useSettings()
  const syms: string[] = settings?.watchlist?.crypto || []
  const { data: tk, error } = useApi<any[]>(syms.length ? `/api/crypto/tickers?syms=${syms.join(',')}` : null, 60000)
  const { data: mk } = useApi<any>('/api/crypto/markets?n=150', 120000)
  const { data: sig } = useApi<any[]>(syms.length ? `/api/signals?kind=crypto&syms=${syms.join(',')}` : null, 120000)
  const { data: dv } = useApi<any>('/api/crypto/derivs?n=400', 60000)
  const [sort, setSort] = useState<[string, number]>(['', 0])
  const ccy = settings?.display?.currency || 'USD'
  const mkBy = useMemo(() => new Map((mk?.rows || []).map((r: any) => [r.sym, r])), [mk])
  const sgBy = useMemo(() => new Map((sig || []).map((r: any) => [r.sym, r])), [sig])
  const dvBy = useMemo(() => new Map((dv?.rows || []).map((r: any) => [r.sym, r])), [dv])
  const rows = useMemo(() => {
    const r = [...(tk || [])]
    const [k, d] = sort
    if (!k) return r
    const val = (t: any): number => {
      const m: any = mkBy.get(t.sym)
      const s: any = sgBy.get(t.sym)
      return (
        ({
          chg24: t.chg24,
          chg1h: m?.chg1h,
          chg7d: m?.chg7d,
          mcap: m?.mcap,
          score: s?.score,
          rsi: s?.t1h?.rsi,
          fund: (dvBy.get(t.sym) as any)?.fund8h,
          sent: s?.sentiment?.sentiment,
        } as Record<string, number>)[k] ?? -1e18
      )
    }
    return r.sort((a, b) => (val(b) - val(a)) * d)
  }, [tk, sort, mkBy, sgBy, dvBy])
  const th = (k: string, l: string, num = true) => (
    <th className={`sortable ${num ? 'num' : ''}`} onClick={() => setSort(([sk, sd]) => [k, sk === k ? -sd || 1 : 1])}>
      {l}
      {sort[0] === k ? (sort[1] > 0 ? ' ↓' : ' ↑') : ''}
    </th>
  )
  return (
    <Panel title="Watchlist" sub="live prices direct from exchange" flush right={<a className="btn ghost sm" href="#/settings">edit</a>}>
      <Err e={error} />
      <div className="scroll">
        <table className="t dense">
          <thead>
            <tr>
              <th>Asset</th>
              <th className="num">Price</th>
              {th('chg1h', '1h')}
              {th('chg24', '24h')}
              {th('chg7d', '7d')}
              <th>7d chart</th>
              {th('mcap', 'Mcap')}
              {th('fund', 'Funding 8h')}
              {th('rsi', 'RSI 1h')}
              <th>EMA 1d</th>
              {th('sent', 'Sentiment', false)}
              {th('score', 'Score', false)}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <WatchRow key={t.sym} t={t} mk={mkBy.get(t.sym)} sig={sgBy.get(t.sym)} fund={dvBy.get(t.sym)} ccy={ccy} rate={rate} />
            ))}
          </tbody>
        </table>
        {!tk && <div style={{ padding: '0.75rem' }}><Skel n={8} h={18} /></div>}
      </div>
    </Panel>
  )
}

// ------------------------------------------------------------------ AI brief
function Brief() {
  const { data, setData, reload } = useApi<any>('/api/ai/brief')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => hub.on('brief', (ev) => setData(ev)), [setData])
  const gen = async () => {
    setBusy(true)
    setErr(null)
    try {
      setData(await post('/api/ai/brief'))
    } catch (e) {
      setErr((e as Error).message)
      reload()
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel
      title={
        <span className="row">
          <Sparkles size={13} className="accent" /> Market brief
        </span>
      }
      sub={data ? `${ago(data.ts)} ago · ${data.model || ''}` : 'AI'}
      right={
        <button className="btn ghost sm" onClick={gen} disabled={busy} title="Generate a fresh brief">
          <RefreshCw size={12} className={busy ? 'spin' : ''} /> {busy ? 'writing…' : 'refresh'}
        </button>
      }
      bodyStyle={{ maxHeight: 470, overflow: 'auto' }}
    >
      <Err e={err} />
      {data?.content ? <Markdown text={data.content} /> : busy ? <Skel n={9} /> : <div className="empty">No brief yet. Generate one, or wait for the scheduled run.</div>}
    </Panel>
  )
}

// ------------------------------------------------------------------ funding heatmap
function FundingHeat() {
  const { data } = useApi<any>('/api/crypto/derivs?n=48', 60000)
  return (
    <Panel title="Perp funding × OI" sub={data ? `top 48 by OI · ${data.source}` : ''} right={<span className="legend"><span><i style={{ background: 'var(--accent)' }} />crowded long</span><span><i style={{ background: 'var(--ice)' }} />crowded short</span></span>}>
      {!data && <Skel n={5} h={40} />}
      <div className="heat">
        {(data?.rows || []).map((r: any) => {
          const f = r.fund8h
          const a = Math.min(Math.abs(f) / 0.03, 1)
          const bg = f >= 0 ? `rgba(255,138,61,${0.08 + a * 0.5})` : `rgba(140,200,255,${0.08 + a * 0.5})`
          return (
            <div key={r.sym} className="cell" style={{ background: bg }} onClick={() => (location.hash = assetHref(r.sym, 'crypto'))} title={`OI $${big(r.oi)} · 24h ${pct(r.chg24)}`}>
              <b>{r.sym}</b>
              <span>{f >= 0 ? '+' : ''}{f.toFixed(4)}%</span>
              <span className={tone(r.chg24)}>{pct(r.chg24, 1)}</span>
            </div>
          )
        })}
      </div>
    </Panel>
  )
}

// ------------------------------------------------------------------ mindshare + news
function Mindshare() {
  const { data } = useApi<any>('/api/sentiment/overview', 120000)
  const rows = data?.mindshare?.rows?.slice(0, 14) || []
  const mx = Math.max(...rows.map((r: any) => r.mindshare), 1)
  return (
    <Panel title={<Term k="mindshare">News mindshare</Term>} sub={`24h · ${data?.mindshare?.total_mentions ?? '—'} mentions`} flush>
      <table className="t">
        <thead>
          <tr>
            <th>Asset</th>
            <th>Share</th>
            <th className="num"><Term k="velocity">Vel.</Term></th>
            <th>Tone</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r: any) => (
            <tr key={r.sym} className="click" onClick={() => (location.hash = assetHref(r.sym))}>
              <td>{r.sym}</td>
              <td style={{ width: '40%' }}>
                <div className="row">
                  <div className="bar grow">
                    <i style={{ width: `${(r.mindshare / mx) * 100}%`, background: 'var(--ice)' }} />
                  </div>
                  <span className="num small" style={{ width: '2.375rem', textAlign: 'right' }}>
                    {r.mindshare.toFixed(1)}%
                  </span>
                </div>
              </td>
              <td className={`num ${r.velocity >= 2 ? 'accent' : ''}`}>{r.velocity.toFixed(1)}x</td>
              <td>
                <SentChip v={r.sentiment} n={r.mentions} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <div className="empty">Collecting news &amp; social mentions…</div>}
    </Panel>
  )
}

export function NewsList({ items }: { items: any[] }) {
  return (
    <>
      {items.map((n) => (
        <div className="newsi" key={n.id}>
          <span className={`chip ${n.label === 'bullish' ? 'up' : n.label === 'bearish' ? 'down' : ''}`} style={{ minWidth: '3.625rem', justifyContent: 'center' }}>
            {n.label}
          </span>
          <div className="grow">
            <a href={n.url} target="_blank" rel="noreferrer">
              {n.title}
            </a>
            <div className="row tiny muted" style={{ marginTop: '0.1875rem' }}>
              <span>{n.source}</span>
              <span>· {ago(n.published)}</span>
              {n.symbols.slice(0, 4).map((s: string) => (
                <a key={s} className="chip click" href={assetHref(s)} style={{ height: '1.0625rem' }}>
                  {s}
                </a>
              ))}
            </div>
          </div>
        </div>
      ))}
    </>
  )
}

function News() {
  const [kind, setKind] = useState<string>('all')
  const { data, setData } = useApi<any[]>(`/api/news?limit=60${kind !== 'all' ? `&kind=${kind}` : ''}`, 180000)
  useEffect(
    () =>
      hub.on('news', (ev) => {
        if (kind === 'all') setData((d) => [...ev.items, ...(d || [])].filter((x, i, a) => a.findIndex((y) => y.id === x.id) === i).slice(0, 80))
      }),
    [kind, setData],
  )
  return (
    <Panel title="News & social" right={<Seg value={kind} options={[['all', 'All'], ['crypto', 'Crypto'], ['macro', 'Macro'], ['stocks', 'Stocks'], ['brazil', 'BR']]} onChange={setKind} />} flush>
      <div className="scroll" style={{ maxHeight: '32.5rem' }}>
        {!data && <div style={{ padding: '0.75rem' }}><Skel n={8} /></div>}
        <NewsList items={data || []} />
      </div>
    </Panel>
  )
}

// ------------------------------------------------------------------ stocks & macro
function Markets() {
  const { data: q } = useApi<any[]>('/api/stocks/quotes', 60000)
  const { data: m } = useApi<any>('/api/macro', 60000)
  const r = m?.rates
  return (
    <Panel title="Stocks & macro" sub="Yahoo · Treasury · brapi" flush>
      <table className="t">
        <tbody>
          {(m?.quotes || []).map((x: any) => (
            <tr key={x.sym} className="click" onClick={() => (location.hash = assetHref(x.sym, 'stock'))}>
              <td className="muted">{x.label}</td>
              <td className="num">{price(x.price)}</td>
              <td className="num">
                <Delta v={x.chg} />
              </td>
            </tr>
          ))}
          {r && (
            <tr>
              <td className="muted">UST 2Y / 10Y</td>
              <td className="num">
                {r['2y']} / {r['10y']}
              </td>
              <td className={`num ${r.spread_2s10s < 0 ? 'down' : ''}`}>{r.spread_2s10s > 0 ? '+' : ''}{r.spread_2s10s}</td>
            </tr>
          )}
          {(q || []).map((x: any) => (
            <tr key={x.sym} className="click" onClick={() => (location.hash = assetHref(x.sym, 'stock'))}>
              <td>
                <b style={{ fontWeight: 500 }}>{x.sym.replace('.SA', '')}</b>
                {x.sym.endsWith('.SA') && <span className="faint tiny"> B3</span>}
              </td>
              <td className="num">{price(x.price)}</td>
              <td className="num">
                <Delta v={x.chg} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}

// ------------------------------------------------------------------ trending + polymarket + flows
function Trending() {
  const { data } = useApi<any>('/api/crypto/trending', 300000)
  return (
    <Panel title="Trending" sub="CoinGecko · DexScreener" flush>
      <div className="scroll" style={{ maxHeight: '20.625rem' }}>
        <table className="t">
          <tbody>
            {(data?.coins || []).slice(0, 10).map((c: any) => (
              <tr key={c.id} className="click" onClick={() => (location.hash = assetHref(c.sym, 'crypto'))}>
                <td>
                  <div className="sym">
                    <SymIcon sym={c.sym} image={c.image} /> {c.sym} <span className="faint small">#{c.rank ?? '—'}</span>
                  </div>
                </td>
                <td className="num">
                  <Delta v={c.chg24h} d={1} />
                </td>
              </tr>
            ))}
            {(data?.dex || []).slice(0, 6).map((d: any) => (
              <tr key={d.address}>
                <td colSpan={2}>
                  <a href={d.url} target="_blank" rel="noreferrer" className="row small">
                    <span className="chip">{d.chain}</span>
                    <span className="muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '13.75rem' }}>
                      {d.desc || d.address.slice(0, 10)}
                    </span>
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function Polymarket() {
  const { data } = useApi<any[]>('/api/crypto/polymarket', 300000)
  return (
    <Panel title="Prediction markets" sub="Polymarket · crowd odds" flush>
      <div className="scroll" style={{ maxHeight: '20.625rem' }}>
        {(data || []).slice(0, 10).map((m) => {
          const yes = m.outcomes.find((o: any) => /yes/i.test(o.name)) || m.outcomes[0]
          return (
            <a key={m.slug} className="newsi" href={`https://polymarket.com/event/${m.slug}`} target="_blank" rel="noreferrer" style={{ display: 'block' }}>
              <div className="small">{m.q}</div>
              <div className="row" style={{ marginTop: '0.3125rem' }}>
                <div className="bar grow">
                  <i style={{ width: `${yes.p * 100}%`, background: 'var(--ice)' }} />
                </div>
                <span className="num small">{yes.name} {(yes.p * 100).toFixed(0)}%</span>
                <span className="faint tiny">${big(m.vol24, 0)} 24h</span>
              </div>
            </a>
          )
        })}
        {data && !data.length && <div className="empty">No macro/crypto markets right now</div>}
      </div>
    </Panel>
  )
}

function FlowBars({ rows }: { rows: { date: string; flow: number }[] }) {
  const mx = Math.max(...rows.map((r) => Math.abs(r.flow)), 1)
  return (
    <div className="row" style={{ alignItems: 'center', height: '3.375rem', gap: '0.1875rem' }}>
      {rows.map((r) => (
        <div key={r.date} title={`${r.date}: ${r.flow > 0 ? '+' : ''}${r.flow.toFixed(1)}M`} style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <div style={{ height: '50%', display: 'flex', alignItems: 'flex-end' }}>
            {r.flow > 0 && <div style={{ width: '100%', height: `${(r.flow / mx) * 100}%`, background: 'var(--up)', borderRadius: '0.125rem' }} />}
          </div>
          <div style={{ height: '50%' }}>{r.flow < 0 && <div style={{ width: '100%', height: `${(-r.flow / mx) * 100}%`, background: 'var(--down)', borderRadius: '0.125rem' }} />}</div>
        </div>
      ))}
    </div>
  )
}

function Flows() {
  const { data } = useApi<any>('/api/crypto/onchain', 300000)
  const etf = data?.etf || {}
  const sum = (r?: any[]) => (r ? r.slice(-5).reduce((s, x) => s + x.flow, 0) : null)
  return (
    <Panel title="Flows & on-chain" sub="Farside · DefiLlama · mempool">
      {!data && <Skel n={5} />}
      {['btc', 'eth'].map((k) =>
        etf[k] ? (
          <div key={k} style={{ marginBottom: '0.75rem' }}>
            <div className="row small">
              <span className="label">{k.toUpperCase()} spot ETF</span>
              <span className="grow" />
              <span className="muted">5d</span>
              <span className={`num ${tone(sum(etf[k]))}`}>{sum(etf[k])! > 0 ? '+' : ''}{num(sum(etf[k]), 0)}M</span>
            </div>
            <FlowBars rows={etf[k].slice(-12)} />
          </div>
        ) : null,
      )}
      {data && (
        <div className="kv">
          <span>Stablecoin supply</span>
          <span>${big(data.stables?.at(-1)?.v)}</span>
          <span>  30d change</span>
          <span className={tone(data.stables_chg30d)}>{pct(data.stables_chg30d)}</span>
          <span>DeFi TVL</span>
          <span>${big(data.tvl?.at(-1)?.v)}</span>
          <span>BTC hashrate</span>
          <span>{num(data.mempool?.hashrate_eh, 0)} EH/s</span>
          <span>Mempool fee (fast)</span>
          <span>{data.mempool?.fees?.fastestFee ?? '—'} sat/vB</span>
        </div>
      )}
      {data?.stables && (
        <div style={{ marginTop: '0.625rem' }}>
          <span className="tiny faint">stablecoin supply 120d</span>
          <Spark data={data.stables.map((x: any) => x.v)} w={300} h={34} color="var(--ice)" />
        </div>
      )}
    </Panel>
  )
}

// ------------------------------------------------------------------ BTC hero chart
// ------------------------------------------------------------------ Elfa (X / Telegram)
function useElfa() {
  return useApi<any>('/api/elfa/overview', 300000)
}

function XMindshare() {
  const { data } = useElfa()
  const [mode, setMode] = useState<'top' | 'risers'>('top')
  const rows: any[] = data?.trending?.rows || []
  const shown = (mode === 'top' ? rows : [...rows].filter((r) => r.cur >= 20).sort((a, b) => b.chg - a.chg)).slice(0, 14)
  const mx = Math.max(1, ...shown.map((r) => r.cur))
  const configured = data?.status?.configured
  return (
    <Panel title={<Term k="xmentions">X mindshare</Term>} sub={data?.trending?.ts ? `Elfa · 24h · ${ago(data.trending.ts)} ago` : 'Elfa'} flush
      right={<Seg label="Sort" value={mode} options={[['top', 'Top'], ['risers', 'Risers']]} onChange={setMode} />}>
      <table className="t">
        <thead><tr><th>Token</th><th>Mentions 24h</th><th className="num">vs prior</th></tr></thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.sym} className="click" onClick={() => (location.hash = assetHref(r.sym, 'crypto'))}>
              <td><b>{r.sym}</b></td>
              <td style={{ width: '50%' }}>
                <div className="row">
                  <div className="bar grow"><i style={{ width: `${(r.cur / mx) * 100}%`, background: 'var(--ice)' }} /></div>
                  <span className="num small" style={{ width: '3rem', textAlign: 'right' }}>{r.cur.toLocaleString()}</span>
                </div>
              </td>
              <td className="num"><Delta v={r.chg} d={0} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!shown.length && <div className="empty">{configured === false ? 'Add an Elfa key in Settings → API keys.' : 'Waiting for the first Elfa snapshot…'}</div>}
    </Panel>
  )
}

function Narratives() {
  const { data } = useElfa()
  const rows: any[] = data?.narratives?.rows || []
  return (
    <Panel title="Narratives on X" sub={data?.narratives?.ts ? `Elfa · ${ago(data.narratives.ts)} ago` : 'Elfa'} flush>
      <div className="scroll" style={{ maxHeight: '26rem' }}>
        {rows.map((n, i) => (
          <div className="narr" key={i}>
            <div className="small">{n.text}</div>
            <div>{(n.links || []).map((l: string, j: number) => <a key={l} href={l} target="_blank" rel="noreferrer">source {j + 1} ↗</a>)}</div>
          </div>
        ))}
        {!rows.length && <div className="empty">No narratives yet (refreshed once a day).</div>}
      </div>
    </Panel>
  )
}

function Contracts() {
  const { data } = useElfa()
  const rows: any[] = (data?.cas?.rows || []).slice(0, 14)
  return (
    <Panel title="Trending contracts" sub="Elfa · X + Telegram · degen zone" flush>
      <table className="t">
        <thead><tr><th>Token</th><th>Chain</th><th className="num">X</th><th className="num">TG</th><th className="num">Liq.</th><th /></tr></thead>
        <tbody>
          {rows.map((r) => {
            const href = r.chain === 'solana' ? `#/asset/dex/${r.ca}` : `https://dexscreener.com/${r.chain}/${r.ca}`
            return (
              <tr key={r.ca} className="click" onClick={() => (href.startsWith('#') ? (location.hash = href) : window.open(href, '_blank', 'noreferrer'))}>
                <td><b>{r.sym || `${r.ca.slice(0, 4)}…${r.ca.slice(-4)}`}</b> {r.verified === false && <span className="flag unverified">unverified</span>}</td>
                <td className="small muted">{r.chain}</td>
                <td className="num">{r.x || '—'}</td>
                <td className="num">{r.tg || '—'}</td>
                <td className={`num ${(r.liquidity || 0) < 100000 ? 'warn' : ''}`}>{r.liquidity ? `$${big(r.liquidity, 1)}` : '—'}</td>
                <td>{r.early && <span className="chip accent"><Term k="early">early</Term></span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {!rows.length && <div className="empty">No contract data yet.</div>}
      <div className="tiny faint" style={{ padding: '0.5rem 0.75rem' }}>Mentions measure attention, not quality. Most trending contracts go to zero.</div>
    </Panel>
  )
}

function OnchainWatch() {
  const { settings } = useSettings()
  const list: any[] = settings?.watchlist?.dex || []
  const mints = list.map((d) => d.mint).join(',')
  const { data } = useApi<any>(mints ? `/api/dex/prices?mints=${mints}` : null, 10000)
  if (!list.length) return null
  return (
    <Panel title="On-chain watchlist" sub="Jupiter · 10s" flush right={<a className="btn ghost sm" href="#/settings">edit</a>}>
      <table className="t">
        <thead><tr><th>Token</th><th className="num">Price</th><th className="num">24h</th></tr></thead>
        <tbody>
          {list.map((d) => (
            <tr key={d.mint} className="click" onClick={() => (location.hash = `#/asset/dex/${d.mint}`)}>
              <td><b>{d.sym}</b> <span className="faint tiny">{d.name}</span></td>
              <td className="num">{price(data?.[d.mint]?.price)}</td>
              <td className="num"><Delta v={data?.[d.mint]?.chg24} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}

function HeroChart() {
  const [iv, setIv] = useState('1h')
  const [info, setInfo] = useState<ChartInfo>({})
  const [sym, setSym] = useState('BTC')
  const fc = useApi<any[]>('/api/predictions/current', 300000).data
  const coneH: Record<string, number> = { '1m': 4, '5m': 4, '15m': 4, '1h': 24, '4h': 168, '1d': 168 }
  const cp = fc?.find((p) => p.sym === sym && p.h === coneH[iv])
  const cone: Cone | null = cp?.cone ? { t0: cp.issued, price: cp.price, h: cp.h, q: cp.cone } : null
  const lines: any[] = []
  return (
    <Panel
      title={
        <span className="row">
          <Seg label="Hero chart asset" value={sym} options={['BTC', 'ETH', 'SOL']} onChange={setSym} />
          <span className="num h2" style={{ marginLeft: '0.375rem' }}>{price(info.last)}</span>
        </span>
      }
      right={
        <>
          <span className="tiny muted">
            {info.feed || '…'}
            {isNum(info.latency) ? ` · ${info.latency}ms` : ''}
          </span>
          <Seg label="Hero chart interval" value={iv} options={['1m', '5m', '15m', '1h', '4h', '1d']} onChange={setIv} />
          <button className="btn ghost sm" onClick={() => go(`asset/crypto/${sym}`)}>
            open <ArrowUpRight size={12} />
          </button>
        </>
      }
      flush
    >
      <div style={{ height: '23.75rem' }}>
        <Chart sym={sym} kind="crypto" interval={iv} onInfo={(i) => setInfo((x) => ({ ...x, ...i }))} priceLines={lines} cone={cone} />
      </div>
      <div className="legend" style={{ padding: '0.375rem 0.75rem', borderTop: '1px solid var(--line)' }}>
        <span><i style={{ background: '#8cc8ff' }} />EMA9</span>
        <span><i style={{ background: '#ff8a3d' }} />EMA21</span>
        <span><i style={{ background: '#e8b74a' }} />EMA50</span>
        <span><i style={{ background: '#b9c0cb' }} />EMA200</span>
        {cone && <span><i style={{ background: 'rgba(140,200,255,.85)' }} /><Term k="cone">{cone.h >= 168 ? '7d' : `${cone.h}h`} forecast cone (10–90%)</Term></span>}
      </div>
    </Panel>
  )
}

export default function Dashboard() {
  return (
    <div className="col">
      <h1 className="sr-only">Market dashboard</h1>
      <Regime />
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,2.2fr) minmax(18.75rem,1fr)' }}>
        <HeroChart />
        <Forecasts />
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,2.2fr) minmax(18.75rem,1fr)' }}>
        <Watchlist />
        <Brief />
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.6fr) minmax(0,1fr) minmax(0,1fr)' }}>
        <FundingHeat />
        <Liquidations />
        <Mindshare />
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(24rem, 1fr))', alignItems: 'start' }}>
        <XMindshare />
        <Narratives />
        <Contracts />
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.6fr) minmax(0,1fr) minmax(0,1fr)' }}>
        <News />
        <Markets />
        <div className="col">
          <Flows />
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(24rem, 1fr))', alignItems: 'start' }}>
        <Trending />
        <Polymarket />
        <OnchainWatch />
      </div>
      <div className="tiny faint" style={{ textAlign: 'center', padding: '0.375rem 0 0.875rem' }}>
        Rook Market Analyser is a research tool, not financial advice. Data: Binance, Bybit, OKX, Coinbase, CoinGecko, CoinPaprika, DefiLlama, Deribit, Farside, Yahoo, SEC,
        US Treasury, CNN, Polymarket, Elfa, Jupiter, GeckoTerminal, DexScreener, RSS/Reddit. 🐧
      </div>
    </div>
  )
}
