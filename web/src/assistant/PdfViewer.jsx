import { useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { Loader2 } from 'lucide-react'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

// Normalise text so a PDF text-layer line can be matched against the stored chunk text.
const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9%₹]+/g, ' ').trim()

/**
 * Renders a PDF and highlights the lines of the cited passage on the cited page.
 * Matching: every text item (≈ one line) on that page whose normalised text appears inside the
 * normalised cited passage is highlighted. Works well for born-digital PDFs.
 */
export default function PdfViewer({ url, page = 1, highlight = '' }) {
  const hostRef = useRef(null)
  const [state, setState] = useState({ status: 'loading', pages: 0, hits: 0 })
  const [width, setWidth] = useState(0)

  // re-render when the panel is resized (debounced)
  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    let t
    const ro = new ResizeObserver(([e]) => {
      clearTimeout(t)
      t = setTimeout(() => setWidth(Math.round(e.contentRect.width)), 120)
    })
    ro.observe(el)
    return () => { ro.disconnect(); clearTimeout(t) }
  }, [])

  useEffect(() => {
    if (!width || !hostRef.current) return
    let cancelled = false
    const host = hostRef.current
    const target = norm(highlight)
    const task = pdfjs.getDocument({ url })
    const tasks = []

    ;(async () => {
      try {
        const pdf = await task.promise
        if (cancelled) return
        host.querySelectorAll('.pdf-page').forEach((n) => n.remove())
        let hits = 0
        let firstHit = null
        const dpr = Math.min(window.devicePixelRatio || 1, 2)
        for (let n = 1; n <= pdf.numPages; n++) {
          const p = await pdf.getPage(n)
          if (cancelled) return
          const base = p.getViewport({ scale: 1 })
          const viewport = p.getViewport({ scale: (width - 2) / base.width })
          const wrap = document.createElement('div')
          wrap.className = 'pdf-page'
          wrap.style.width = `${viewport.width}px`
          wrap.style.height = `${viewport.height}px`
          wrap.dataset.page = String(n)
          const canvas = document.createElement('canvas')
          canvas.width = Math.floor(viewport.width * dpr)
          canvas.height = Math.floor(viewport.height * dpr)
          canvas.style.width = `${viewport.width}px`
          canvas.style.height = `${viewport.height}px`
          wrap.appendChild(canvas)
          const label = document.createElement('div')
          label.className = 'pdf-page-label'
          label.textContent = `Page ${n} of ${pdf.numPages}`
          wrap.appendChild(label)
          host.appendChild(wrap)
          const rt = p.render({ canvasContext: canvas.getContext('2d'), viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null })
          tasks.push(rt)
          await rt.promise
          if (cancelled) return

          if (n === page && target) {
            const content = await p.getTextContent()
            for (const item of content.items) {
              const t = norm(item.str)
              if (t.length < 5 || !target.includes(t)) continue
              const tx = pdfjs.Util.transform(viewport.transform, item.transform)
              const h = Math.hypot(tx[2], tx[3])
              const mark = document.createElement('div')
              mark.className = 'pdf-hit'
              mark.style.left = `${tx[4] - 2}px`
              mark.style.top = `${tx[5] - h * 0.95}px`
              mark.style.width = `${item.width * viewport.scale + 4}px`
              mark.style.height = `${h * 1.25}px`
              wrap.appendChild(mark)
              hits += 1
              if (!firstHit) firstHit = mark
            }
          }
        }
        if (cancelled) return
        setState({ status: 'ready', pages: pdf.numPages, hits })
        const scrollTarget = firstHit || host.querySelector(`.pdf-page[data-page="${page}"]`)
        if (scrollTarget) {
          const box = host.closest('.ca-panel-body') || host
          const top = scrollTarget.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 90
          box.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
        }
      } catch (e) {
        if (!cancelled) setState({ status: 'error', pages: 0, hits: 0 })
      }
    })()

    return () => {
      cancelled = true
      tasks.forEach((t) => { try { t.cancel() } catch { /* already done */ } })
      task.destroy()
    }
  }, [url, page, highlight, width])

  return (
    <div className="pdf-viewer">
      {state.status === 'loading' && <div className="ca-thinking"><Loader2 size={15} className="ca-spin" /> Opening document…</div>}
      {state.status === 'error' && <div className="ca-notice">This document could not be displayed.</div>}
      {state.status === 'ready' && (
        <div className="pdf-status">{state.hits ? `${state.hits} highlighted line${state.hits > 1 ? 's' : ''} on page ${page}` : `Cited section on page ${page}`}</div>
      )}
      <div ref={hostRef} className="pdf-pages" />
    </div>
  )
}
