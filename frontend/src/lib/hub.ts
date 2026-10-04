// Backend event hub (/ws): liquidations, alerts, news, predictions, briefs.
type Cb = (ev: any) => void

class Hub {
  subs = new Map<string, Set<Cb>>()
  ws: WebSocket | null = null
  backoff = 500
  connected = false
  stateSubs = new Set<(c: boolean) => void>()

  start() {
    if (this.ws) return
    this.connect()
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    this.ws = ws
    ws.onopen = () => {
      this.backoff = 500
      this.setConn(true)
    }
    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data)
        this.subs.get(ev.type)?.forEach((cb) => cb(ev))
        this.subs.get('*')?.forEach((cb) => cb(ev))
      } catch {
        /* ignore */
      }
    }
    ws.onclose = () => {
      this.setConn(false)
      this.ws = null
      setTimeout(() => this.connect(), this.backoff)
      this.backoff = Math.min(this.backoff * 2, 10000)
    }
    ws.onerror = () => ws.close()
  }

  setConn(c: boolean) {
    this.connected = c
    this.stateSubs.forEach((cb) => cb(c))
  }

  on(type: string, cb: Cb) {
    if (!this.subs.has(type)) this.subs.set(type, new Set())
    this.subs.get(type)!.add(cb)
    return () => {
      this.subs.get(type)?.delete(cb)
    }
  }
}

export const hub = new Hub()
