import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: false })

function esc(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string)
}

/** Renders AI markdown; ```forecast blocks become graded-call cards. */
export default function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  const html = useMemo(() => {
    const t = text.replace(/```forecast\s*([\s\S]*?)```/g, (_, body) => {
      try {
        const arr = JSON.parse(body.trim())
        const items = (Array.isArray(arr) ? arr : [arr])
          .map(
            (f: any) =>
              `<div class="fc"><span class="label">graded call</span><br/><b>${esc(String(f.symbol))}</b> ${esc(String(f.horizon_h))}h · P(up) <b class="${f.p_up >= 0.5 ? 'up' : 'down'}">${(f.p_up * 100).toFixed(0)}%</b>${f.note ? `<br/><span class="muted">${esc(String(f.note))}</span>` : ''}</div>`,
          )
          .join('')
        return `\n<div class="fc-card">${items}</div>\n`
      } catch {
        return streaming ? '' : '<div class="fc-card"><div class="fc muted">forecast block (unparsed)</div></div>'
      }
    })
    // hide a half-streamed forecast fence
    const clean = streaming ? t.replace(/```forecast[\s\S]*$/, '') : t
    return DOMPurify.sanitize(marked.parse(clean) as string)
  }, [text, streaming])
  return <div className={`md ${streaming ? 'cursor' : ''}`} dangerouslySetInnerHTML={{ __html: html }} />
}
