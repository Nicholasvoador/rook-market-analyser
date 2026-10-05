import { useEffect, useState } from 'react'
import { Bell, CandlestickChart, LayoutDashboard, MessageSquare, Radar, Search, Settings as Cog, Target, Wallet } from 'lucide-react'
import CommandPalette from './components/CommandPalette'
import { Flash } from './components/ui'
import { useApi } from './lib/api'
import { pct, price, tone } from './lib/fmt'
import { hub } from './lib/hub'
import { live, probeVenues } from './lib/live'
import { applyScale, assetHref, go, loadSettings, saveSettings, setRate, setStockSet, useLive, useRoute, useSettings } from './lib/store'
import Alerts from './pages/Alerts'
import Ask from './pages/Ask'
import Asset from './pages/Asset'
import Dashboard from './pages/Dashboard'
import Portfolio from './pages/Portfolio'
import Markets from './pages/Markets'
import Predictions from './pages/Predictions'
import Settings from './pages/Settings'
import Signals from './pages/Signals'

const NAV: [string, string, typeof LayoutDashboard, string][] = [
  ['dashboard', 'Dashboard', LayoutDashboard, '1'],
  ['markets', 'Markets', CandlestickChart, '2'],
  ['ask', 'Ask Rook', MessageSquare, '3'],
  ['signals', 'Signals', Radar, '4'],
  ['predictions', 'Predictions', Target, '5'],
  ['portfolio', 'Portfolio', Wallet, '6'],
  ['alerts', 'Alerts', Bell, '7'],
  ['settings', 'Settings', Cog, '8'],
]

function TapeItem({ sym }: { sym: string }) {
  const t = useLive(sym)
  return (
    <a className="tape-item" href={assetHref(sym, 'crypto')}>
      <b>{sym}</b>
      <Flash v={t?.price} className="num">
        {price(t?.price)}
      </Flash>
      <span className={`num small ${tone(t?.chg24)}`}>{pct(t?.chg24)}</span>
    </a>
  )
}

function Tape() {
  const { settings } = useSettings()
  const { data: macro } = useApi<any>('/api/macro', 60000)
  useEffect(() => {
    if (macro?.usdbrl?.rate) setRate(macro.usdbrl.rate)
  }, [macro])
  const cr: string[] = settings?.watchlist?.crypto || []
  const mq = (macro?.quotes || []).filter((q: any) => q.price != null)
  const once = (k: string) => (
    <>
      {cr.map((s) => (
        <TapeItem key={`${k}${s}`} sym={s} />
      ))}
      {mq.map((q: any) => (
        <a key={`${k}${q.sym}`} className="tape-item" href={assetHref(q.sym, 'stock')}>
          <b>{q.label}</b>
          <span className="num">{price(q.price)}</span>
          <span className={`num small ${tone(q.chg)}`}>{pct(q.chg)}</span>
        </a>
      ))}
    </>
  )
  return (
    <div className="tape">
      <div className="tape-inner">
        {once('a')}
        {once('b')}
      </div>
    </div>
  )
}

const SCALE_MIN = 0.9
const SCALE_MAX = 1.6

function ScaleButtons() {
  const { settings } = useSettings()
  const cur: number = settings?.display?.ui_scale ?? 1.15
  const set = (v: number) => {
    const s = Math.round(Math.min(SCALE_MAX, Math.max(SCALE_MIN, v)) * 100) / 100
    applyScale(s)
    saveSettings({ display: { ui_scale: s } })
  }
  return (
    <div className="scalebtns" role="group" aria-label="Interface size">
      <button onClick={() => set(cur - 0.05)} aria-label="Smaller interface" title="Smaller (Ctrl -)">A−</button>
      <span aria-live="polite">{Math.round(cur * 100)}%</span>
      <button onClick={() => set(cur + 0.05)} aria-label="Larger interface" title="Larger (Ctrl +)">A+</button>
    </div>
  )
}

function Status() {
  const [hubOk, setHubOk] = useState(hub.connected)
  const [ex, setEx] = useState(false)
  const { data } = useApi<any>('/api/status', 20000)
  useEffect(() => {
    const f = (c: boolean) => setHubOk(c)
    hub.stateSubs.add(f)
    const id = setInterval(() => setEx(live.connected() && Date.now() - live.lastMsg < 15000), 1500)
    return () => {
      hub.stateSubs.delete(f)
      clearInterval(id)
    }
  }, [])
  const ai = data?.hermes?.ok
  const rows: [string, boolean | undefined, string][] = [
    ['Exchange feed', ex, ex ? 'direct WS' : 'connecting'],
    ['Backend', hubOk, hubOk ? 'live' : 'offline'],
    ['Hermes AI', ai, ai ? 'ready' : 'unreachable'],
  ]
  return (
    <div className="side-foot">
      {rows.map(([l, ok, s]) => (
        <div className="row small" key={l} title={`${l}: ${s}`}>
          <span className={`dot ${ok ? 'ok' : ok === false ? 'bad' : ''}`} />
          <span className="txt muted">{l}</span>
        </div>
      ))}
    </div>
  )
}

type Toast = { id: number; title: string; body: string }

export default function App() {
  const route = useRoute()
  const { settings } = useSettings()
  const [pal, setPal] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])

  useEffect(() => {
    loadSettings().catch(() => undefined)
    hub.start()
    const offA = hub.on('alert', (ev) => {
      const t = { id: Date.now(), title: ev.name, body: ev.message }
      setToasts((x) => [...x, t].slice(-4))
      setTimeout(() => setToasts((x) => x.filter((y) => y.id !== t.id)), 12000)
      if ('Notification' in window && Notification.permission === 'granted' && document.visibilityState !== 'visible')
        new Notification(`Rook · ${ev.name}`, { body: ev.message })
    })
    // measure exchange feed latency in the background so streams pick the fastest venue
    window.setTimeout(() => probeVenues().catch(() => undefined), 2500)
    const key = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPal((p) => !p)
      } else if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') {
        e.preventDefault()
        setPal(true)
      } else if ((e.ctrlKey || e.metaKey) && (e.key === '=' || e.key === '+' || e.key === '-')) {
        // scale the UI (rem-based) instead of browser zoom, so it persists in Settings
        e.preventDefault()
        const cur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1.15
        const s = Math.round(Math.min(SCALE_MAX, Math.max(SCALE_MIN, cur + (e.key === '-' ? -0.05 : 0.05))) * 100) / 100
        applyScale(s)
        saveSettings({ display: { ui_scale: s } })
      } else if (e.altKey && /^[1-8]$/.test(e.key)) {
        const n = NAV[+e.key - 1]
        if (n) go(n[0])
      }
    }
    window.addEventListener('keydown', key)
    return () => {
      offA()
      window.removeEventListener('keydown', key)
    }
  }, [])

  useEffect(() => {
    if (settings) {
      setStockSet([...(settings.watchlist?.stocks || []), ...(settings.macro || []).map((m: any) => m.sym)])
      const d = settings.display || {}
      document.documentElement.dataset.density = d.density || 'comfortable'
      document.documentElement.dataset.contrast = d.high_contrast ? 'high' : 'normal'
      document.documentElement.dataset.motion = d.reduce_motion ? 'reduce' : 'normal'
      applyScale(d.ui_scale ?? 1.15)
      if (d.accent) document.documentElement.style.setProperty('--accent', d.accent)
    }
  }, [settings])

  const page = route.page
  return (
    <div className="shell">
      <a className="skip-link" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus() }}>
        Skip to content
      </a>
      <aside className="side" aria-label="Main navigation">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <span className="brand-name">Rook <span className="faint" style={{ fontWeight: 400 }}>Market Analyser</span></span>
        </div>
        {NAV.map(([p, l, Icon, k]) => (
          <a key={p} className={`nav ${page === p || (p === 'markets' && page === 'asset') ? 'on' : ''}`} aria-current={page === p ? 'page' : undefined} href={`#/${p}`}>
            <Icon aria-hidden="true" />
            <span>{l}</span>
            <span className="k">⌥{k}</span>
          </a>
        ))}
        <Status />
      </aside>
      <header className="top">
        <button className="search" onClick={() => setPal(true)} aria-label="Search or ask (Ctrl K)">
          <Search size={14} aria-hidden="true" />
          <span>Search or ask…</span>
          <kbd>Ctrl K</kbd>
        </button>
        <Tape />
        <ScaleButtons />
      </header>
      <main className="main" id="main" tabIndex={-1}>
        {page === 'dashboard' && <Dashboard />}
        {page === 'asset' && <Asset kind={(route.parts[1] as 'crypto' | 'stock' | 'dex') || 'crypto'} sym={decodeURIComponent(route.parts[2] || 'BTC')} />}
        {page === 'markets' && <Markets tab={route.parts[1]} />}
        {page === 'ask' && <Ask chatId={route.parts[1]} q={route.query.get('q')} />}
        {page === 'signals' && <Signals />}
        {page === 'predictions' && <Predictions />}
        {page === 'portfolio' && <Portfolio />}
        {page === 'alerts' && <Alerts />}
        {page === 'settings' && <Settings />}
      </main>
      <CommandPalette open={pal} onClose={() => setPal(false)} />
      <div className="toast-wrap" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast" key={t.id} onClick={() => go('alerts')}>
            <div className="row">
              <Bell size={14} className="accent" />
              <b>{t.title}</b>
            </div>
            <div className="small muted" style={{ marginTop: '0.25rem' }}>
              {t.body}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
