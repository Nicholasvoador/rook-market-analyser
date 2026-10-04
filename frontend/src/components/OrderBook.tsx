import { useEffect, useRef, useState } from 'react'
import { bookStream, type Book, type Trade } from '../lib/live'
import { big, clock, price } from '../lib/fmt'

export default function OrderBook({ sym, rows = 11 }: { sym: string; rows?: number }) {
  const [book, setBook] = useState<Book | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const pending = useRef<{ book?: Book; trades: Trade[] }>({ trades: [] })

  useEffect(() => {
    setBook(null)
    setTrades([])
    let raf = 0
    const flush = () => {
      raf = 0
      const p = pending.current
      if (p.book) setBook(p.book)
      if (p.trades.length) {
        const add = p.trades
        setTrades((t) => [...add.reverse(), ...t].slice(0, 40))
      }
      pending.current = { trades: [] }
    }
    const sched = () => {
      if (!raf) raf = requestAnimationFrame(flush)
    }
    const stop = bookStream(
      sym,
      (b) => {
        pending.current.book = b
        sched()
      },
      (t) => {
        pending.current.trades.push(t)
        sched()
      },
    )
    return () => {
      stop()
      cancelAnimationFrame(raf)
    }
  }, [sym])

  if (!book) return <div className="empty">Connecting to order book…</div>
  const asks = book.asks.slice(0, rows).reverse()
  const bids = book.bids.slice(0, rows)
  const maxQ = Math.max(...asks.map((a) => a[0] * a[1]), ...bids.map((b) => b[0] * b[1]), 1)
  const spread = book.asks[0] && book.bids[0] ? book.asks[0][0] - book.bids[0][0] : 0
  const mid = book.asks[0] && book.bids[0] ? (book.asks[0][0] + book.bids[0][0]) / 2 : 0
  const bidV = bids.reduce((s, b) => s + b[0] * b[1], 0)
  const askV = asks.reduce((s, a) => s + a[0] * a[1], 0)
  const imb = bidV + askV ? (bidV - askV) / (bidV + askV) : 0

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 0 }}>
      <div className="book" style={{ borderRight: '1px solid var(--line)' }}>
        <div className="lv tiny muted" style={{ padding: '0.3125rem 0.75rem' }}>
          <span>PRICE</span>
          <span>SIZE $</span>
        </div>
        {asks.map(([p, q]) => (
          <div className="lv" key={`a${p}`}>
            <i style={{ width: `${((p * q) / maxQ) * 100}%`, background: 'var(--down)' }} />
            <span className="down">{price(p)}</span>
            <span>{big(p * q, 1)}</span>
          </div>
        ))}
        <div className="lv" style={{ padding: '0.3125rem 0.75rem', borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line)' }}>
          <span>{price(mid)}</span>
          <span className="muted">
            spr {((spread / (mid || 1)) * 1e4).toFixed(1)}bp
          </span>
        </div>
        {bids.map(([p, q]) => (
          <div className="lv" key={`b${p}`}>
            <i style={{ width: `${((p * q) / maxQ) * 100}%`, background: 'var(--up)' }} />
            <span className="up">{price(p)}</span>
            <span>{big(p * q, 1)}</span>
          </div>
        ))}
        <div className="lv tiny" style={{ padding: '0.375rem 0.75rem' }}>
          <span className="muted">book imbalance</span>
          <span className={imb > 0 ? 'up' : 'down'}>{(imb * 100).toFixed(0)}%</span>
        </div>
      </div>
      <div className="book scroll" style={{ maxHeight: rows * 2 * 19 + 90 }}>
        <div className="lv tiny muted" style={{ padding: '0.3125rem 0.75rem', gridTemplateColumns: '1fr 1fr 1fr' }}>
          <span>PRICE</span>
          <span style={{ textAlign: 'right' }}>SIZE $</span>
          <span style={{ textAlign: 'right' }}>TIME</span>
        </div>
        {trades.map((t, i) => (
          <div className="lv" key={`${t.t}-${i}`} style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
            <span className={t.buy ? 'up' : 'down'}>{price(t.p)}</span>
            <span style={{ textAlign: 'right' }}>{big(t.p * t.q, 1)}</span>
            <span className="faint" style={{ textAlign: 'right' }}>
              {clock(t.t / 1000)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
