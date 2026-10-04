import { useEffect, useState } from 'react'
import { Eye, EyeOff, Plus, RefreshCw, Star, Trash2 } from 'lucide-react'
import { Delta, Err, Panel, Skel, Term, Toggle } from './ui'
import { api, del, post, put, useApi } from '../lib/api'
import { ago, big, money, num, price } from '../lib/fmt'
import { loadSettings, useSettings } from '../lib/store'

const FLAG_HELP: Record<string, string> = {
  ok: 'Verified with real liquidity: counted in your portfolio.',
  unverified: 'Not on Jupiter\'s verified list (mostly airdropped spam or copies). Not counted.',
  illiquid: 'The position is too large for the token\'s on-chain liquidity to be realisable. Not counted.',
  unpriced: 'No price from Jupiter or DexScreener. Not counted.',
}

function WalletCard({ w, onChange }: { w: any; onChange: () => void }) {
  const { settings, rate } = useSettings()
  const ccy = settings?.display?.currency || 'USD'
  const [snap, setSnap] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [reveal, setReveal] = useState(false)
  const [all, setAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const load = async (force = false) => {
    setBusy(true)
    setErr(null)
    try {
      setSnap(await api(`/api/wallets/${w.id}/snapshot${force ? '?force=true' : ''}`))
    } catch (e) {
      setErr((e as Error).message)
    }
    setBusy(false)
  }
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [w.id])
  const watch = async (t: any) => {
    await post('/api/watchlist', t.cex ? { kind: 'crypto', sym: t.cex } : { kind: 'dex', mint: t.mint, sym: t.symbol, name: t.name })
    await loadSettings()
  }
  const wl = settings?.watchlist || {}
  const watched = (t: any) => (t.cex ? (wl.crypto || []).includes(t.cex) : (wl.dex || []).some((d: any) => d.mint === t.mint))
  const toks = (snap?.tokens || []).filter((t: any) => all || (t.flag === 'ok' && !t.dust))
  const hidden = (snap?.tokens || []).length - toks.length
  return (
    <div className="panel" style={{ marginBottom: '0.625rem' }}>
      <div className="panel-h">
        <button className="linkish" onClick={() => setOpen(!open)} aria-expanded={open} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <span className="t">{w.label}</span>
          <span className="addr">{reveal ? w.address : w.masked}</span>
        </button>
        <button className="btn ghost sm" onClick={() => setReveal(!reveal)} aria-label={reveal ? 'Hide address' : 'Show full address'} title={reveal ? 'Hide address' : 'Show full address'}>
          {reveal ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
        <div className="r">
          {snap && <b className="num">{money(snap.total_ok_usd, ccy, rate)}</b>}
          {snap && <span className="tiny muted">{ago(snap.ts)} ago</span>}
          <span className="tiny muted">in portfolio</span>
          <Toggle label={`Include ${w.label} in portfolio`} on={!!w.include} onChange={async (v) => { await put(`/api/wallets/${w.id}`, { include: v }); onChange() }} />
          <button className="btn ghost sm" onClick={() => load(true)} disabled={busy} aria-label="Refresh balances"><RefreshCw size={13} /></button>
          <button className="btn ghost sm danger" aria-label={`Stop tracking ${w.label}`} onClick={async () => { if (confirm(`Stop tracking ${w.label}? Its address is deleted from the local database.`)) { await del(`/api/wallets/${w.id}`); onChange() } }}><Trash2 size={13} /></button>
        </div>
      </div>
      <Err e={err} />
      {!snap && !err && <div style={{ padding: '0.75rem' }}><Skel n={2} /></div>}
      {snap && (
        <div className="row wrap small" style={{ padding: '0.5rem 0.75rem', gap: '0.75rem' }}>
          {Object.entries(snap.counts || {}).map(([f, n]) => (
            <span key={f} className={`flag ${f}`} title={FLAG_HELP[f]}>{f}: {n as number}</span>
          ))}
          {snap.partial && <span className="warn tiny">partial: an RPC call failed, some tokens may be missing</span>}
          <span className="grow" />
          <button className="btn ghost sm" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide tokens' : `Show tokens (${toks.length})`}</button>
        </div>
      )}
      {snap && open && (
        <>
          <table className="t">
            <thead>
              <tr>
                <th>Token</th>
                <th className="num">Amount</th>
                <th className="num">Price</th>
                <th className="num">24h</th>
                <th className="num">Value</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {toks.slice(0, 200).map((t: any) => (
                <tr key={t.mint} className="click" onClick={() => (location.hash = t.cex ? `#/asset/crypto/${t.cex}` : `#/asset/dex/${t.mint}`)}>
                  <td>
                    <span className="row">
                      {t.icon ? <img className="tok-icon" src={t.icon} alt="" loading="lazy" /> : <span className="tok-icon" />}
                      <b>{t.symbol || `${t.mint.slice(0, 4)}…`}</b>
                      <span className="faint tiny">{t.cex ? 'CEX-listed' : 'on-chain'}</span>
                    </span>
                  </td>
                  <td className="num">{num(t.amount, t.amount < 10 ? 4 : 2)}</td>
                  <td className="num">{price(t.price)}</td>
                  <td className="num"><Delta v={t.chg24} /></td>
                  <td className="num">{t.value_usd != null ? money(t.value_usd, ccy, rate) : '—'}</td>
                  <td><span className={`flag ${t.flag}`} title={FLAG_HELP[t.flag]}>{t.flag}</span></td>
                  <td>
                    {t.flag === 'ok' && !watched(t) && (
                      <button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); watch(t) }} title={t.cex ? `Add ${t.cex} to the crypto watchlist` : 'Add to the on-chain watchlist'}>
                        <Star size={12} /> Watch
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row small" style={{ padding: '0.5rem 0.75rem' }}>
            {hidden > 0 && !all && <span className="muted">{hidden} hidden (dust under ${settings?.solana?.hide_dust_usd ?? 1}, or <Term k="verified">unverified</Term>/illiquid/unpriced)</span>}
            <span className="grow" />
            <button className="btn ghost sm" onClick={() => setAll(!all)}>{all ? 'Hide spam & dust' : 'Show everything'}</button>
          </div>
        </>
      )}
    </div>
  )
}

export default function Wallets({ onChange }: { onChange?: () => void }) {
  const { data, reload } = useApi<any[]>('/api/wallets')
  const { secrets } = useSettings()
  const [addr, setAddr] = useState('')
  const [label, setLabel] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const changed = async () => {
    await reload()
    onChange?.()
  }
  const add = async () => {
    setBusy(true)
    setErr(null)
    try {
      await post('/api/wallets', { address: addr.trim(), label: label.trim(), chain: 'solana' })
      setAddr('')
      setLabel('')
      await changed()
    } catch (e) {
      setErr((e as Error).message)
    }
    setBusy(false)
  }
  const rpc = secrets?.SOLANA_RPC_URL?.set ? 'your own RPC (Settings → API keys)' : 'the public Solana RPC'
  return (
    <Panel title="Solana wallets" sub="watch-only · balances priced live">
      <div className="row wrap" style={{ marginBottom: '0.5rem' }}>
        <label className="sr-only" htmlFor="w-addr">Solana wallet address</label>
        <input id="w-addr" className="input mono" placeholder="Wallet address (public key)" value={addr} onChange={(e) => setAddr(e.target.value)} style={{ width: '28rem', maxWidth: '100%' }} autoComplete="off" spellCheck={false} />
        <label className="sr-only" htmlFor="w-label">Label</label>
        <input id="w-label" className="input" placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} style={{ width: '12rem' }} />
        <button className="btn primary" onClick={add} disabled={busy || addr.trim().length < 32}><Plus aria-hidden="true" /> Track wallet</button>
      </div>
      <Err e={err} />
      <div className="tiny faint" style={{ marginBottom: '0.625rem' }}>
        Watch-only: no keys, no signing. The address is stored only in the local database (never in settings, git or AI prompts) and balances are read from {rpc}. Spam airdrops are flagged and excluded from totals.
      </div>
      {!data && <Skel n={2} />}
      {data?.map((w) => <WalletCard key={w.id} w={w} onChange={changed} />)}
      {data && !data.length && <div className="muted small">No wallets yet. Paste a Solana address to track SOL and SPL/Token-2022 balances.</div>}
      {data && data.length > 0 && <div className="tiny muted">Wallet tokens appear in Holdings below (flag = ok, above dust) and count toward risk.</div>}
      {data && data.length > 0 && <span className="sr-only">{big(data.length)} wallets tracked</span>}
    </Panel>
  )
}
