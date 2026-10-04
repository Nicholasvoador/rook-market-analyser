import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Plus, Square, Trash2, Wrench, X } from 'lucide-react'
import Markdown from '../components/Markdown'
import { Panel, Seg } from '../components/ui'
import { api, del, streamSSE, useApi } from '../lib/api'
import { ago } from '../lib/fmt'
import { go, useRoute } from '../lib/store'

type Msg = {
  role: 'user' | 'assistant'
  content: string
  meta?: any
  reasoning?: string
  tools?: { name: string; label: string }[]
  notices?: string[]
  model?: string
  status?: string
  streaming?: boolean
  error?: string
  forecasts?: { sym: string; h: number; p: number }[]
  symbols?: string[]
}

const SUGGEST: [string, string][] = [
  ['Why is the market moving?', 'What is driving crypto right now? Separate signal from noise using flows, positioning and news.'],
  ['Trade setup', 'Give me the best risk/reward setup in my watchlist right now, with entry, invalidation and size.'],
  ['Mindshare movers', 'Which assets are gaining mindshare (news and X/Elfa) and is the price confirming the attention?'],
  ['X narratives → trades', 'Which trending X narratives map to liquid assets in my watchlist, and is any of them a setup worth taking?'],
  ['Portfolio review', 'Review my portfolio: concentration, correlation, risk vs my profile, and what you would change.'],
  ['Is positioning crowded?', 'Is BTC/ETH perp positioning crowded? Funding, OI, long/short and liquidation risk on both sides.'],
  ['Macro → crypto', 'Macro check: yields, DXY, equities, VIX. What does it imply for crypto this week?'],
  ['Compare fundamentals', 'Compare ETH vs SOL vs HYPE on fundamentals: valuation, fees/revenue, supply, momentum.'],
  ['Brazil angle', 'USD/BRL and Ibovespa: what should I watch, and how does it affect buying crypto from Brazil?'],
]

const PLAIN = 'Explain your last answer in plain language for someone who is not a trader: no jargon, short sentences, keep the key numbers and levels, and end with one sentence on what I should actually do or watch.'

function AssistantMsg({ m, onFollow }: { m: Msg; onFollow?: (q: string) => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="msg">
      <div className="meta">
        {m.model && <span className="chip accent">{m.model}</span>}
        {m.meta?.attempt && m.meta.attempt !== 'primary' && <span className="chip ice">fallback: {m.meta.attempt}</span>}
        {m.symbols?.map((s) => (
          <span key={s} className="chip">{s}</span>
        ))}
        {m.tools && m.tools.length > 0 && (
          <span className="chip" title={m.tools.map((t) => `${t.name}: ${t.label}`).join('\n')}>
            <Wrench size={11} /> {m.tools.length} tool call{m.tools.length > 1 ? 's' : ''}
          </span>
        )}
        {m.streaming && m.status && <span className="small muted">{m.status}</span>}
      </div>
      {m.notices?.map((n, i) => (
        <div key={i} className="small warn" style={{ marginBottom: '0.375rem' }}>
          ↻ {n}
        </div>
      ))}
      {m.reasoning && (
        <details open={m.streaming && !m.content}>
          <summary className="small muted" style={{ cursor: 'pointer', marginBottom: '0.375rem' }}>
            reasoning
          </summary>
          <div className="reason">{m.reasoning}</div>
        </details>
      )}
      {m.streaming && m.tools && m.tools.length > 0 && !m.content && (
        <div className="small muted">
          <Wrench size={11} /> {m.tools.at(-1)!.name} {m.tools.at(-1)!.label}
        </div>
      )}
      {m.content ? <Markdown text={m.content} streaming={m.streaming} /> : m.streaming ? <div className="md cursor small muted">thinking</div> : null}
      {m.error && <div className="err" style={{ padding: '0.375rem 0' }}>{m.error}</div>}
      {m.forecasts && m.forecasts.length > 0 && (
        <div className="tiny muted" style={{ marginTop: '0.375rem' }}>
          ✓ {m.forecasts.length} probability call{m.forecasts.length > 1 ? 's' : ''} logged: graded automatically when they mature (see Predictions → LLM track record).
        </div>
      )}
      {!m.streaming && m.content && (
        <div className="row" style={{ marginTop: '0.5rem', gap: '0.375rem' }}>
          {onFollow && <button className="btn ghost sm" onClick={() => onFollow(PLAIN)}>Explain simply</button>}
          {onFollow && <button className="btn ghost sm" onClick={() => onFollow('What would make this view wrong? Give the specific levels, data or events to watch, and how likely each is.')}>What could go wrong?</button>}
          <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(m.content || ''); setCopied(true); setTimeout(() => setCopied(false), 1200) }}>{copied ? 'Copied' : 'Copy'}</button>
        </div>
      )}
    </div>
  )
}

export default function Ask({ chatId, q }: { chatId?: string; q?: string | null }) {
  const route = useRoute()
  const { data: chats, reload: reloadChats } = useApi<any[]>('/api/ai/chats')
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [mode, setMode] = useState<'fast' | 'expert'>('fast')
  const [focus, setFocus] = useState<string[]>([])
  const [focusIn, setFocusIn] = useState('')
  const [busy, setBusy] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const ownId = useRef<string | undefined>(undefined)
  const scroller = useRef<HTMLDivElement>(null)
  const autoSent = useRef<string | null>(null)

  // load an existing chat (but not the one we're currently streaming into)
  useEffect(() => {
    if (!chatId) {
      if (!busy) setMsgs([])
      return
    }
    if (chatId === ownId.current) return
    api(`/api/ai/chats/${chatId}`).then((d) =>
      setMsgs(
        d.messages.map((m: any) => ({
          role: m.role,
          content: m.content,
          model: m.meta?.model,
          tools: m.meta?.tools,
          forecasts: m.meta?.forecasts,
        })),
      ),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [msgs])

  const send = async (msg: string, f = focus) => {
    if (!msg.trim() || busy) return
    setText('')
    setBusy(true)
    const ac = new AbortController()
    abort.current = ac
    setMsgs((m) => [...m, { role: 'user', content: msg }, { role: 'assistant', content: '', streaming: true, status: 'Connecting…', tools: [], notices: [] }])
    const patch = (fn: (m: Msg) => Msg) =>
      setMsgs((ms) => {
        const c = [...ms]
        c[c.length - 1] = fn(c[c.length - 1])
        return c
      })
    try {
      await streamSSE(
        '/api/ai/chat',
        { chat_id: chatId, message: msg, mode, focus: f },
        (ev) => {
          switch (ev.type) {
            case 'chat':
              if (ev.id !== chatId) {
                ownId.current = ev.id
                history.replaceState(null, '', `#/ask/${ev.id}`)
                window.dispatchEvent(new HashChangeEvent('hashchange'))
              }
              break
            case 'status':
              patch((m) => ({ ...m, status: ev.text }))
              break
            case 'context':
              patch((m) => ({ ...m, symbols: ev.symbols, status: `Context: ${ev.chars.toLocaleString()} chars of live data` }))
              break
            case 'meta':
              patch((m) => ({ ...m, model: ev.model, meta: ev, status: 'Thinking…', content: ev.attempt === 'primary' ? m.content : m.content }))
              break
            case 'delta':
              patch((m) => ({ ...m, content: m.content + ev.text }))
              break
            case 'reasoning':
              patch((m) => ({ ...m, reasoning: (m.reasoning || '') + ev.text }))
              break
            case 'tool':
              patch((m) => ({ ...m, tools: [...(m.tools || []), ev], status: `${ev.name} ${ev.label}` }))
              break
            case 'notice':
              patch((m) => ({ ...m, notices: [...(m.notices || []), ev.text] }))
              break
            case 'done':
              patch((m) => ({ ...m, content: ev.content, model: ev.model, streaming: false }))
              break
            case 'forecasts':
              patch((m) => ({ ...m, forecasts: ev.items }))
              break
            case 'error':
              patch((m) => ({ ...m, error: ev.text, streaming: false }))
              break
          }
        },
        ac.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') patch((m) => ({ ...m, error: (e as Error).message }))
    } finally {
      patch((m) => ({ ...m, streaming: false }))
      setBusy(false)
      abort.current = null
      reloadChats()
    }
  }

  // auto-send ?q= (from palette / asset page)
  useEffect(() => {
    if (q && autoSent.current !== q) {
      autoSent.current = q
      const f = route.query.get('focus')
      const fl = f ? f.split(',') : []
      if (fl.length) setFocus(fl)
      ownId.current = undefined
      setMsgs([])
      setTimeout(() => send(q, fl), 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])

  const newChat = () => {
    ownId.current = undefined
    setMsgs([])
    go('ask')
  }

  return (
    <div className="chat-wrap">
      <Panel title="Conversations" right={<button className="btn ghost sm" onClick={newChat}><Plus size={12} /> new</button>} flush>
        <div className="chat-list" style={{ padding: '0.375rem' }}>
          {(chats || []).map((c) => (
            <div key={c.id} className={`ci ${c.id === chatId ? 'on' : ''}`} onClick={() => go(`ask/${c.id}`)}>
              <span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title}</span>
              <span className="faint tiny">{ago(c.updated)}</span>
              <button
                className="btn ghost sm"
                style={{ padding: '0 0.25rem', height: '1.25rem' }}
                onClick={async (e) => {
                  e.stopPropagation()
                  await del(`/api/ai/chats/${c.id}`)
                  if (c.id === chatId) newChat()
                  reloadChats()
                }}
              >
                <Trash2 size={11} />
              </button>
            </div>
          ))}
          {chats && !chats.length && <div className="empty">No conversations yet</div>}
        </div>
      </Panel>
      <section className="panel chat-main">
        <div className="msgs" ref={scroller}>
          {!msgs.length && (
            <div className="col" style={{ margin: 'auto 0', gap: '1.125rem' }}>
              <div style={{ maxWidth: '53.75rem', margin: '0 auto', width: '100%' }}>
                <h1 className="h1" style={{ margin: 0 }}>Ask Rook</h1>
                <div className="muted" style={{ marginTop: '0.25rem' }}>
                  Every answer gets a live snapshot: prices, EMAs/RSI, levels, active setups and their track record, funding/OI, liquidations, ETF flows, news tone, X mindshare and
                  narratives (Elfa), macro, your portfolio (weights only) and the models' track record. Answers start with a bottom line and a confidence level. Runs on your
                  Hermes models, falling back to Gemini 3.8 at max reasoning.
                </div>
              </div>
              <div className="suggest">
                {SUGGEST.map(([t, p]) => (
                  <button key={t} onClick={() => send(p)}>
                    <b>{t}</b>
                    <span className="small">{p}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m, i) =>
            m.role === 'user' ? (
              <div className="msg user" key={i}>
                <div className="bubble">{m.content}</div>
              </div>
            ) : (
              <AssistantMsg key={i} m={m} onFollow={i === msgs.length - 1 && !busy ? (q) => send(q) : undefined} />
            ),
          )}
        </div>
        <div className="composer">
          <div className="box">
            <textarea
              aria-label="Message"
              value={text}
              placeholder="Ask about any coin, stock, setup, narrative or your portfolio…  (Enter to send, Shift+Enter for newline)"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send(text)
                }
              }}
            />
            <div className="row">
              <Seg label="Answer depth" value={mode} options={[['fast', 'Fast'], ['expert', 'Expert · max reasoning']]} onChange={(v) => setMode(v)} />
              {focus.map((f) => (
                <span key={f} className="chip">
                  {f}
                  <button className="linkish" style={{ display: 'inline' }} aria-label={`Remove focus ${f}`} onClick={() => setFocus(focus.filter((x) => x !== f))}><X size={10} /></button>
                </span>
              ))}
              <input
                className="input"
                style={{ height: '1.625rem', width: '8rem', fontSize: '0.8125rem' }}
                placeholder="+ focus symbol"
                aria-label="Add focus symbol"
                value={focusIn}
                onChange={(e) => setFocusIn(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && focusIn.trim()) {
                    setFocus([...new Set([...focus, focusIn.trim()])])
                    setFocusIn('')
                  }
                }}
              />
              <span className="grow" />
              {busy ? (
                <button className="btn" onClick={() => abort.current?.abort()}>
                  <Square size={12} /> stop
                </button>
              ) : (
                <button className="btn primary" onClick={() => send(text)} disabled={!text.trim()}>
                  <ArrowUp size={14} /> send
                </button>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
