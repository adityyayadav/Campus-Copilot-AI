import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BookOpen, Database, ExternalLink, FileText, Loader2, X } from 'lucide-react'
import { getJSON } from '../api.js'
import PdfViewer from './PdfViewer.jsx'

// Markers the answer model emits: [D1], [SQL], [D1, D3], (D2), (see D1–D6), [D1-D3], and the full-width 【…】 variant.
const ID = String.raw`(?:SQL|D\d+)`
const SEP = String.raw`\s*(?:,|;|&|and|–|—|-|to)\s*`
const LIST = String.raw`(?:see\s+|sources?:?\s+|ref\.?\s+)?(${ID}(?:${SEP}${ID})*)`
const MARKER = new RegExp(String.raw`\s?(?:\[\s*${LIST}\s*\]|\(\s*${LIST}\s*\)|【([^】]*)】)`, 'gi')

/** "D1–D3, SQL" -> ["D1","D2","D3","SQL"] (ranges expanded) */
function parseIds(list) {
  const out = []
  const re = /(SQL|D(\d+))(?:\s*(?:–|—|-|to)\s*D(\d+))?/gi
  let m
  while ((m = re.exec(list))) {
    if (m[1].toUpperCase() === 'SQL') out.push('SQL')
    else {
      const a = Number(m[2]); const b = m[3] ? Number(m[3]) : a
      for (let i = a; i <= Math.min(b, a + 20); i++) out.push(`D${i}`)
    }
  }
  return out
}

// surrogate keys mean nothing to a reader
const INTERNAL_COLS = new Set(['txn_id', 'slot_id', 'exam_id', 'application_id'])

/** Markdown -> readable plain text for the short hover preview. */
const plain = (md) => (md || '')
  .replace(/\*\*|__|`/g, '')
  .replace(/^\s*[-*]\s+/gm, '• ')
  .replace(/^\s*\|?[-:\s|]+\|?\s*$/gm, '')
  .replace(/\|/g, ' ')
  .replace(/\n{2,}/g, '\n')
  .trim()

const titleCase = (s) => s.replace(/_/g, ' ').replace(/\bpct\b/i, '%').replace(/\b\w/g, (c) => c.toUpperCase())
const lastSection = (section) => (section || '').split(' > ').pop().trim()

function sourceFromId(id, data) {
  if (id === 'SQL') {
    const s = data?.sql
    if (!s || s.error || !s.rows?.length) return null
    const columns = s.columns.filter((c) => !INTERNAL_COLS.has(c))
    return { key: 'SQL', kind: 'records', title: 'University records', subtitle: 'Student information system · live data',
      columns: columns.length ? columns : s.columns, rows: s.rows }
  }
  const idx = Number(id.slice(1)) - 1
  const c = data?.docs?.chunks?.[idx]
  if (!c) return null
  if (c.collection === 'table_rows') {
    return { key: id, kind: 'catalogue', title: 'University catalogue', subtitle: titleCase(c.section || 'record'), excerpt: c.content }
  }
  const isPdf = c.doc_type === 'pdf' || (c.source || '').toLowerCase().endsWith('.pdf')
  return { key: id, kind: 'policy', title: c.title, subtitle: lastSection(c.section) + (isPdf && c.page ? ` · p. ${c.page}` : ''),
    section: c.section, excerpt: c.content, source: c.source, isPdf, page: c.page || 1 }
}

/**
 * Turns marker-annotated answer text into markdown with numbered citation links (#cite-N),
 * numbered in order of first appearance, plus the ordered list of cited sources.
 */
export function useCitations(answer, data) {
  return useMemo(() => {
    const order = []
    const byKey = {}
    const text = (answer || '').replace(MARKER, (_m, a, b, c) => {
      const ids = parseIds(a || b || c || '')
      const nums = []
      for (const id of ids) {
        if (!(id in byKey)) {
          const src = sourceFromId(id, data)
          if (!src) continue
          order.push(src)
          byKey[id] = order.length
        }
        if (!nums.includes(byKey[id])) nums.push(byKey[id])
      }
      return nums.length ? ' ' + nums.map((n) => `[${n}](#cite-${n})`).join('') : ''
    })
    // nothing cited explicitly: still show what the answer was based on
    if (!order.length && data) {
      if (sourceFromId('SQL', data)) order.push(sourceFromId('SQL', data))
      const seen = new Set()
      ;(data.docs?.chunks || []).forEach((_, i) => {
        const s = sourceFromId(`D${i + 1}`, data)
        const k = s && `${s.title}|${s.subtitle}`
        if (s && !seen.has(k)) { seen.add(k); order.push(s) }
      })
      return { text, sources: order.slice(0, 6), inline: false }
    }
    return { text, sources: order, inline: true }
  }, [answer, data])
}

function SourceIcon({ kind, size = 14 }) {
  return kind === 'policy' ? <FileText size={size} /> : <Database size={size} />
}

/** Inline numbered chip with a hover preview card. */
export function CitationChip({ n, source, onOpen }) {
  const ref = useRef(null)
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState(null)
  const timer = useRef(null)
  const suppressed = useRef(false)  // after a click, don't re-open the preview until the pointer leaves

  const show = () => { if (suppressed.current) return; clearTimeout(timer.current); timer.current = setTimeout(() => setOpen(true), 120) }
  const hide = () => { suppressed.current = false; clearTimeout(timer.current); timer.current = setTimeout(() => setOpen(false), 120) }

  useLayoutEffect(() => {
    if (!open || !ref.current) return
    const r = ref.current.getBoundingClientRect()
    const w = 320
    const left = Math.max(12, Math.min(window.innerWidth - w - 12, r.left + r.width / 2 - w / 2))
    const above = r.top > 220
    setPos({ left, top: above ? r.top - 8 : r.bottom + 8, above })
  }, [open])

  if (!source) return null
  return (
    <>
      <button ref={ref} type="button" className="ca-cite" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
        onClick={(e) => { e.preventDefault(); clearTimeout(timer.current); suppressed.current = true; setOpen(false); onOpen(source) }} aria-label={`Source ${n}: ${source.title}`}>
        {n}
      </button>
      {open && pos && createPortal(
        <div className={`ca-pop ${pos.above ? 'above' : ''}`} style={{ left: pos.left, top: pos.top }} onMouseEnter={show} onMouseLeave={hide}>
          <div className="ca-pop-head"><SourceIcon kind={source.kind} /> <span>{source.title}</span></div>
          {source.subtitle && <div className="ca-pop-sub">{source.subtitle}</div>}
          {source.kind === 'records'
            ? <div className="ca-pop-text">{source.rows.length} record{source.rows.length > 1 ? 's' : ''} used for this answer</div>
            : <div className="ca-pop-text">{plain(source.excerpt)}</div>}
          <div className="ca-pop-foot">Click to open</div>
        </div>, document.body)}
    </>
  )
}

/** Compact numbered source cards shown under an answer. */
export function SourceList({ sources, inline, onOpen }) {
  if (!sources.length) return null
  return (
    <div className="ca-srclist">
      <div className="ca-srclist-label">{inline ? 'Sources' : 'Based on'}</div>
      <div className="ca-srccards">
        {sources.map((s, i) => (
          <button key={s.key} className="ca-srccard" onClick={() => onOpen(s)}>
            {inline && <span className="ca-srcnum">{i + 1}</span>}
            <SourceIcon kind={s.kind} />
            <span className="ca-srccard-text">
              <span className="ca-srccard-title">{s.title}</span>
              {s.subtitle && <span className="ca-srccard-sub">{s.subtitle}</span>}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
const textOf = (children) => (Array.isArray(children) ? children.map(textOf).join('') : typeof children === 'object' && children ? textOf(children.props?.children) : String(children ?? ''))

/** Right-hand panel: cited passage + full document (policies) or the exact records used (database). */
export function SourcePanel({ source, onClose, onResize }) {
  const [doc, setDoc] = useState(null)
  const [showDoc, setShowDoc] = useState(false)
  const [err, setErr] = useState(null)
  const bodyRef = useRef(null)

  useEffect(() => { setDoc(null); setShowDoc(false); setErr(null) }, [source?.key, source?.source])
  // PDFs: load metadata (issuer, effective date) for the footer
  useEffect(() => {
    if (source?.isPdf && source.source) getJSON(`/api/documents/${encodeURIComponent(source.source)}`).then(setDoc).catch(() => {})
  }, [source?.isPdf, source?.source])

  // drag the left edge to resize the split screen
  function startResize(e) {
    e.preventDefault()
    const move = (ev) => onResize?.(Math.round(window.innerWidth - ev.clientX))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('ca-resizing') }
    document.body.classList.add('ca-resizing')
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  function openFull() {
    setShowDoc(true)
    if (doc || !source?.source) return
    getJSON(`/api/documents/${encodeURIComponent(source.source)}`).then(setDoc).catch(() => setErr('This document could not be loaded.'))
  }

  // scroll the full document to the cited section and highlight its heading
  useEffect(() => {
    if (!showDoc || !doc || !bodyRef.current) return
    const target = slug(lastSection(source.section))
    const el = bodyRef.current.querySelector(`[data-slug="${target}"]`)
    if (el) { el.classList.add('hit'); el.scrollIntoView({ block: 'start', behavior: 'smooth' }) }
  }, [showDoc, doc, source])

  if (!source) return null
  const heading = (Tag) => ({ children }) => {
    const s = slug(textOf(children))
    return <Tag data-slug={s}>{children}</Tag>
  }

  return (
    <aside className={`ca-panel ${source.isPdf ? 'pdf' : ''}`} aria-label="Source">
      <div className="ca-resize" onPointerDown={startResize} role="separator" aria-orientation="vertical" aria-label="Resize source panel" />
      <div className="ca-panel-head">
        <span className={`ca-panel-icon ${source.kind}`}><SourceIcon kind={source.kind} size={16} /></span>
        <div className="ca-panel-titles">
          <div className="ca-panel-title">{source.title}</div>
          {source.subtitle && <div className="ca-panel-sub">{source.subtitle}</div>}
        </div>
        <button className="ca-icon" onClick={onClose} aria-label="Close source"><X size={18} /></button>
      </div>

      <div className="ca-panel-body" ref={bodyRef}>
        {source.kind === 'records' ? (
          <>
            <p className="ca-panel-note">These are the exact records the answer was based on, read live from the student information system with your access permissions.</p>
            <div className="ca-panel-table">
              <table>
                <thead><tr>{source.columns.map((c) => <th key={c}>{titleCase(c)}</th>)}</tr></thead>
                <tbody>{source.rows.map((r, i) => <tr key={i}>{source.columns.map((c) => <td key={c}>{String(r[c] ?? '-')}</td>)}</tr>)}</tbody>
              </table>
            </div>
          </>
        ) : source.isPdf ? (
          <>
            <div className="ca-evidence-bar">
              <span>Evidence explorer · cited lines are highlighted on page {source.page}</span>
              <a href={`/api/documents/${encodeURIComponent(source.source)}/file#page=${source.page}`} target="_blank" rel="noreferrer">
                <ExternalLink size={13} /> Open original
              </a>
            </div>
            <PdfViewer url={`/api/documents/${encodeURIComponent(source.source)}/file`} page={source.page} highlight={source.excerpt} />
          </>
        ) : (
          <>
            <div className="ca-panel-label">Cited passage</div>
            <blockquote className="ca-quote"><ReactMarkdown remarkPlugins={[remarkGfm]}>{source.excerpt}</ReactMarkdown></blockquote>
            {source.kind === 'policy' && !showDoc && (
              <button className="ca-panel-btn" onClick={openFull}><BookOpen size={15} /> View full document</button>
            )}
            {showDoc && (
              <div className="ca-doc">
                <div className="ca-panel-label">Full document</div>
                {err ? <div className="ca-notice">{err}</div> : !doc ? (
                  <div className="ca-thinking"><Loader2 size={15} className="ca-spin" /> Loading document…</div>
                ) : (
                  <div className="ca-md ca-doc-md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ h1: heading('h1'), h2: heading('h2'), h3: heading('h3'), h4: heading('h4') }}>
                      {doc.markdown}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
      {source.kind === 'policy' && doc?.meta?.owner && (
        <div className="ca-panel-foot"><ExternalLink size={13} /> Issued by {doc.meta.owner}{doc.meta.effective_from ? ` · effective ${doc.meta.effective_from}` : ''}</div>
      )}
    </aside>
  )
}
