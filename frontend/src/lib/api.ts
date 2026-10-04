import { useCallback, useEffect, useRef, useState } from 'react'

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      msg = j.detail || msg
    } catch {
      /* not json */
    }
    throw new Error(msg)
  }
  return r.json()
}

export const post = <T = any>(p: string, body?: unknown) => api<T>(p, { method: 'POST', body: JSON.stringify(body ?? {}) })
export const put = <T = any>(p: string, body: unknown) => api<T>(p, { method: 'PUT', body: JSON.stringify(body) })
export const del = <T = any>(p: string) => api<T>(p, { method: 'DELETE' })

const cache = new Map<string, { t: number; v: unknown }>()

/** Fetch + optional polling. Serves cached data instantly (stale-while-revalidate), pauses when tab hidden. */
export function useApi<T = any>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | undefined>(() => (path ? (cache.get(path)?.v as T) : undefined))
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const alive = useRef(true)
  const cur = useRef(path)
  cur.current = path

  const load = useCallback(async () => {
    if (!path) return
    setLoading(true)
    try {
      const v = await api<T>(path)
      cache.set(path, { t: Date.now(), v })
      if (alive.current && cur.current === path) {
        setData(v)
        setError(null)
      }
    } catch (e) {
      if (alive.current && cur.current === path) setError((e as Error).message)
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [path])

  useEffect(() => {
    alive.current = true
    if (path) {
      const c = cache.get(path)
      setData(c ? (c.v as T) : undefined)
    }
    load()
    let id: number | undefined
    if (refreshMs > 0) {
      id = window.setInterval(() => {
        if (document.visibilityState === 'visible') load()
      }, refreshMs)
    }
    return () => {
      alive.current = false
      if (id) clearInterval(id)
    }
  }, [path, refreshMs, load])

  return { data, error, loading, reload: load, setData }
}

/** Server-sent events over fetch POST (used for AI streaming). */
export async function streamSSE(path: string, body: unknown, onEvent: (ev: any) => void, signal?: AbortSignal) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`)
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            onEvent(JSON.parse(line.slice(6)))
          } catch {
            /* ignore partial */
          }
        }
      }
    }
  }
}
