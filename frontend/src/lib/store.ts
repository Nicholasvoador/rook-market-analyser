import { useEffect, useState, useSyncExternalStore } from 'react'
import { api, put } from './api'
import { live, type Tick } from './live'

// ------------------------------------------------------------------ settings store
type Settings = any
let settings: Settings | null = null
let secrets: Record<string, { label: string; set: boolean }> = {}
let rate = 5.2
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())

export async function loadSettings() {
  const r = await api('/api/settings')
  settings = r.settings
  secrets = r.secrets
  notify()
}

export async function saveSettings(patch: Settings) {
  const r = await put('/api/settings', patch)
  settings = r.settings
  secrets = r.secrets
  notify()
}

export function applyScale(s: number) {
  document.documentElement.style.setProperty('--ui-scale', String(s))
}

export function setRate(r: number) {
  if (r && r !== rate) {
    rate = r
    notify()
  }
}

let snap = { settings, secrets, rate }
function getSnap() {
  if (snap.settings !== settings || snap.secrets !== secrets || snap.rate !== rate) snap = { settings, secrets, rate }
  return snap
}

export function useSettings() {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    getSnap,
  )
}

// ------------------------------------------------------------------ live price hook
export function useLive(sym: string | null) {
  const [t, setT] = useState<Tick | undefined>(() => (sym ? live.prices.get(sym) : undefined))
  useEffect(() => {
    if (!sym) return
    let raf = 0
    let latest: Tick | undefined
    const un = live.subscribe(sym, (x) => {
      latest = x
      if (!raf)
        raf = requestAnimationFrame(() => {
          raf = 0
          setT(latest)
        })
    })
    return () => {
      un()
      cancelAnimationFrame(raf)
    }
  }, [sym])
  return t
}

// ------------------------------------------------------------------ hash router
function parse() {
  const h = location.hash.replace(/^#\/?/, '')
  const [path, query] = h.split('?')
  const parts = path.split('/').filter(Boolean)
  return { page: parts[0] || 'dashboard', parts, query: new URLSearchParams(query || '') }
}

let route = parse()
const rlis = new Set<() => void>()
window.addEventListener('hashchange', () => {
  route = parse()
  rlis.forEach((l) => l())
})

export function useRoute() {
  return useSyncExternalStore(
    (cb) => {
      rlis.add(cb)
      return () => rlis.delete(cb)
    },
    () => route,
  )
}

export const go = (p: string) => {
  location.hash = p.startsWith('#') ? p : `#/${p.replace(/^\//, '')}`
}

export function assetHref(sym: string, kind?: string) {
  const k = kind || (/[.^=]/.test(sym) || isStockLike(sym) ? 'stock' : 'crypto')
  return `#/asset/${k}/${encodeURIComponent(sym)}`
}

let stockSet = new Set<string>()
export function setStockSet(s: string[]) {
  stockSet = new Set(s)
}
function isStockLike(sym: string) {
  return stockSet.has(sym)
}
