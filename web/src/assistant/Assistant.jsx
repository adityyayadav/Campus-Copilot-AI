import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowUp, Check, ChevronDown, Copy, GraduationCap, Menu, MessageSquare, PenSquare, Square, X,
} from 'lucide-react'
import { getJSON, streamChat } from '../api.js'
import { CitationChip, SourceList, SourcePanel, useCitations } from './citations.jsx'
import './assistant.css'

const STORE_KEY = 'campus-copilot-chats-v1'
const USER_KEY = 'campus-copilot-user-v1'

const SUGGESTIONS = {
  student: [
    'Which research grants am I eligible for, and when do they close?',
    'Do I have any pending fees? Will I be charged a late fine?',
    'Am I eligible to get my end-semester hall ticket?',
    'Which scholarships can I apply for right now?',
  ],
  admin: [
    'How many students have pending exam fees this term?',
    'Show the fee status of student S1045.',
    'Which third-year CSE students have a GPA above 3.5?',
    'Which open research grants have no applications yet?',
  ],
  guest: [
    'How many seats are left in the Generative AI elective?',
    'What is the refund if I withdraw 20 days after the term starts?',
    'What documents do I need to apply for a research grant?',
    'What are the hostel mess timings?',
  ],
}

const STATUS = {
  start: 'Understanding your question',
  records: 'Checking university records',
  policies: 'Reading university policies',
  both: 'Checking records and policies',
  writing: 'Writing your answer',
  verifying: 'Double-checking the details',
}

// ------------------------------------------------------------------ helpers
const uid = () => Math.random().toString(36).slice(2, 10)
const firstName = (p) => (p && p.role !== 'admin' ? p.name.split(' ')[0] : null)
const initials = (p) => (p ? (p.role === 'admin' ? 'RO' : p.name.split(' ').map((s) => s[0]).join('')) : 'G')
const accountMeta = (p) => (!p ? 'Public information only' : p.role === 'admin' ? 'Administrator · all records' : `${p.department} · Year ${p.year}`)

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

function loadJSON(key, fallback) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback } catch { return fallback }
}
function saveJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ }
}

/** Remove internal citation markers like [SQL], [D1], (D1, D3) for a clean reading experience. */
function cleanAnswer(text) {
  return (text || '')
    .replace(/\s?\[(?:SQL|D\d+)(?:\s*,\s*(?:SQL|D\d+))*\]/g, '')
    .replace(/\s?\((?:SQL|D\d+)(?:\s*,\s*(?:SQL|D\d+))*\)/g, '')
    .replace(/【[^】]*】/g, '')
}

function statusFrom(events) {
  const { route, strategy, node } = events
  if (node === 'synthesize') return STATUS.verifying
  if (node === 'docs' && strategy === 'sequential') return STATUS.records
  if (node === 'sql' || node === 'docs') return STATUS.writing
  if (node === 'router') {
    if (route === 'SQL_ONLY') return STATUS.records
    if (route === 'VECTOR_ONLY') return STATUS.policies
    if (route === 'HYBRID') return strategy === 'sequential' ? STATUS.policies : STATUS.both
  }
  return STATUS.start
}

// ------------------------------------------------------------------ app
export default function Assistant() {
  const [personas, setPersonas] = useState([])
  const [userId, setUserId] = useState(() => loadJSON(USER_KEY, 'S1023'))
  // drop a remembered account that no longer exists in the demo list
  useEffect(() => { if (personas.length && userId && !personas.some((p) => p.student_id === userId)) setUserId('S1023') }, [personas, userId])
  const [chats, setChats] = useState(() => loadJSON(STORE_KEY, []))
  const [activeId, setActiveId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)
  const [panel, setPanelState] = useState(null)
  const [panelW, setPanelW] = useState(460)
  // PDFs open as a wide split screen; other sources as a side panel
  const setPanel = (src) => {
    setPanelState(src)
    if (src) setPanelW(src.isPdf ? Math.round(Math.max(560, window.innerWidth * 0.46)) : 460)
  }
  const resizePanel = (w) => setPanelW(Math.max(360, Math.min(w, Math.round(window.innerWidth * 0.72))))
  const abortRef = useRef(null)
  const scrollRef = useRef(null)

  useEffect(() => { getJSON('/api/personas').then(setPersonas).catch(() => setPersonas([])) }, [])
  useEffect(() => { saveJSON(STORE_KEY, chats.slice(0, 30)) }, [chats])
  useEffect(() => { saveJSON(USER_KEY, userId) }, [userId])

  const profile = personas.find((p) => p.student_id === userId) || null
  const userChats = useMemo(() => chats.filter((c) => c.userId === userId), [chats, userId])
  const active = userChats.find((c) => c.id === activeId) || null

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [active?.messages])

  function updateChat(id, fn) {
    setChats((cs) => cs.map((c) => (c.id === id ? fn(c) : c)))
  }
  function updateMsg(chatId, msgId, patch) {
    updateChat(chatId, (c) => ({ ...c, messages: c.messages.map((m) => (m.id === msgId ? { ...m, ...patch(m) } : m)) }))
  }

  function newChat() {
    abortRef.current?.abort()
    setActiveId(null)
    setSidebarOpen(false)
    setPanel(null)
  }

  function switchUser(id) {
    abortRef.current?.abort()
    setUserId(id)
    setActiveId(null)
    setAccountOpen(false)
    setPanel(null)
  }

  async function send(text) {
    const question = text.trim()
    if (!question || busy) return
    let chat = active
    if (!chat) {
      chat = { id: uid(), userId, title: question.length > 48 ? question.slice(0, 46) + '…' : question, messages: [], updated: Date.now() }
      setChats((cs) => [chat, ...cs])
      setActiveId(chat.id)
    }
    const history = chat.messages
      .filter((m) => m.status !== 'blocked' && m.status !== 'error')
      .map((m) => ({ role: m.role, content: m.role === 'user' ? m.content : (m.answer || '') }))
      .filter((m) => m.content)
      .slice(-6)
      .map((m) => ({ ...m, content: m.content.slice(0, 1500) }))
    const um = { id: uid(), role: 'user', content: question }
    const am = { id: uid(), role: 'assistant', status: 'thinking', statusText: STATUS.start, answer: '', data: null }
    const chatId = chat.id
    updateChat(chatId, (c) => ({ ...c, updated: Date.now(), messages: [...c.messages, um, am] }))
    setBusy(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    const ev = { route: null, strategy: null, node: null }
    try {
      await streamChat({ question, student_id: userId || null, history, use_rerank: true, use_hyde: false }, (e) => {
        if (e.type === 'step') {
          ev.node = e.node
          if (e.data?.route) { ev.route = e.data.route; ev.strategy = e.data.router?.strategy }
          updateMsg(chatId, am.id, () => ({ statusText: statusFrom(ev) }))
        } else if (e.type === 'final') {
          updateMsg(chatId, am.id, () => ({ status: 'done', answer: e.answer, data: { sql: e.sql, docs: e.docs } }))
        } else if (e.type === 'blocked') {
          updateMsg(chatId, am.id, () => ({ status: 'blocked', answer: e.message }))
        } else if (e.type === 'error') {
          updateMsg(chatId, am.id, () => ({ status: 'error', answer: e.message }))
        }
      }, ctrl.signal)
    } catch (err) {
      if (err.name === 'AbortError') updateMsg(chatId, am.id, (m) => ({ status: 'stopped', answer: m.answer || 'Stopped.' }))
      else updateMsg(chatId, am.id, () => ({ status: 'error', answer: "I couldn't reach the server. Please check your connection and try again." }))
    } finally {
      updateMsg(chatId, am.id, (m) => (m.status === 'thinking' ? { status: 'error', answer: 'The response was interrupted. Please try again.' } : {}))
      setBusy(false)
    }
  }

  function stop() { abortRef.current?.abort() }

  function deleteChat(id) {
    setChats((cs) => cs.filter((c) => c.id !== id))
    if (activeId === id) setActiveId(null)
  }

  return (
    <div className={`ca ${panel ? 'with-panel' : ''}`} style={panel ? { '--panel-w': `${panelW}px` } : undefined}>
      <aside className={`ca-side ${sidebarOpen ? 'open' : ''}`}>
        <div className="ca-side-top">
          <div className="ca-brand">
            <span className="ca-logo"><GraduationCap size={18} /></span>
            <span>Campus Copilot</span>
          </div>
          <button className="ca-icon ca-only-mobile" onClick={() => setSidebarOpen(false)} aria-label="Close menu"><X size={18} /></button>
        </div>
        <button className="ca-new" onClick={newChat}><PenSquare size={16} /> New chat</button>

        <div className="ca-history">
          {userChats.length > 0 && <div className="ca-side-label">Recent</div>}
          {userChats.map((c) => (
            <div key={c.id} className={`ca-hist-item ${c.id === activeId ? 'active' : ''}`}>
              <button className="ca-hist-btn" onClick={() => { setActiveId(c.id); setSidebarOpen(false) }} title={c.title}>
                <MessageSquare size={14} /> <span>{c.title}</span>
              </button>
              <button className="ca-hist-del" onClick={() => deleteChat(c.id)} aria-label="Delete conversation"><X size={13} /></button>
            </div>
          ))}
        </div>

        <div className="ca-account">
          <button className="ca-account-btn" onClick={() => setAccountOpen((v) => !v)} aria-expanded={accountOpen}>
            <span className={`ca-avatar ${profile?.role === 'admin' ? 'admin' : ''}`}>{initials(profile)}</span>
            <span className="ca-account-text">
              <span className="ca-account-name">{profile ? profile.name : 'Guest'}</span>
              <span className="ca-account-meta">{accountMeta(profile)}</span>
            </span>
            <ChevronDown size={16} />
          </button>
          {accountOpen && (
            <div className="ca-menu" role="menu">
              <div className="ca-menu-label">Switch account (demo)</div>
              {personas.map((p) => (
                <button key={p.student_id} className={`ca-menu-item ${p.student_id === userId ? 'on' : ''}`} onClick={() => switchUser(p.student_id)}>
                  <span>{p.name}</span>
                  <span className="ca-menu-sub">{p.role === 'admin' ? 'Admin' : `Student · ${p.dept_id}`}</span>
                  {p.student_id === userId && <Check size={14} />}
                </button>
              ))}
              <button className={`ca-menu-item ${!userId ? 'on' : ''}`} onClick={() => switchUser('')}>
                <span>Guest</span><span className="ca-menu-sub">Public info</span>{!userId && <Check size={14} />}
              </button>
              <div className="ca-menu-sep" />
              <a className="ca-menu-item" href="/console">Developer console</a>
            </div>
          )}
        </div>
      </aside>
      {sidebarOpen && <div className="ca-scrim" onClick={() => setSidebarOpen(false)} />}

      <main className="ca-main">
        <header className="ca-top">
          <button className="ca-icon ca-only-mobile" onClick={() => setSidebarOpen(true)} aria-label="Open menu"><Menu size={18} /></button>
          <span className="ca-top-title">{active ? active.title : 'Campus Copilot'}</span>
          <button className="ca-icon ca-only-mobile" onClick={newChat} aria-label="New chat"><PenSquare size={17} /></button>
        </header>

        <div className="ca-scroll" ref={scrollRef}>
          {!active ? (
            <Welcome profile={profile} onPick={send} />
          ) : (
            <div className="ca-thread">
              {active.messages.map((m) => (m.role === 'user'
                ? <div key={m.id} className="ca-user"><div className="ca-user-bubble">{m.content}</div></div>
                : <AssistantMessage key={m.id} m={m} onOpenSource={setPanel} />))}
            </div>
          )}
        </div>

        <Composer onSend={send} onStop={stop} busy={busy} profile={profile} />
      </main>
      {panel && <SourcePanel source={panel} onClose={() => setPanel(null)} onResize={resizePanel} />}
    </div>
  )
}

function Welcome({ profile, onPick }) {
  const list = !profile ? SUGGESTIONS.guest : profile.role === 'admin' ? SUGGESTIONS.admin : SUGGESTIONS.student
  return (
    <div className="ca-welcome">
      <div className="ca-welcome-logo"><GraduationCap size={26} /></div>
      <h1>{greeting()}{firstName(profile) ? `, ${firstName(profile)}` : ''}</h1>
      <p>Ask me about fees, grants, scholarships, courses, exams or campus policies.</p>
      <div className="ca-suggest">
        {list.map((q) => <button key={q} onClick={() => onPick(q)}>{q}</button>)}
      </div>
    </div>
  )
}

function AssistantMessage({ m, onOpenSource }) {
  const [copied, setCopied] = useState(false)
  const { text, sources, inline } = useCitations(m.answer, m.data)
  const components = useMemo(() => ({
    a: ({ href, children }) => {
      if (href?.startsWith('#cite-')) {
        const n = Number(href.slice(6))
        return <CitationChip n={n} source={sources[n - 1]} onOpen={onOpenSource} />
      }
      return <a href={href} target="_blank" rel="noreferrer">{children}</a>
    },
  }), [sources, onOpenSource])

  function copy() {
    navigator.clipboard?.writeText(cleanAnswer(m.answer)).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {})
  }

  return (
    <div className="ca-bot">
      <span className="ca-bot-avatar"><GraduationCap size={15} /></span>
      <div className="ca-bot-body">
        {m.status === 'thinking' ? (
          <div className="ca-thinking"><span className="ca-dots"><i /><i /><i /></span>{m.statusText}…</div>
        ) : m.status === 'blocked' || m.status === 'error' ? (
          <div className="ca-notice">{m.answer}</div>
        ) : (
          <>
            <div className="ca-md"><ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown></div>
            <SourceList sources={sources} inline={inline} onOpen={onOpenSource} />
            <div className="ca-actions">
              <button className="ca-act" onClick={copy} aria-label="Copy answer">{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy'}</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function Composer({ onSend, onStop, busy, profile }) {
  const [text, setText] = useState('')
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [text])
  const submit = () => { if (text.trim() && !busy) { onSend(text); setText('') } }
  return (
    <div className="ca-composer-wrap">
      <div className="ca-composer">
        <textarea
          id="ca-input"
          ref={ref}
          rows={1}
          maxLength={1000}
          value={text}
          placeholder={!profile ? 'Ask about courses, grants, scholarships or policies' : 'Message Campus Copilot'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
          aria-label="Message"
        />
        {busy
          ? <button className="ca-send" onClick={onStop} aria-label="Stop"><Square size={14} fill="currentColor" /></button>
          : <button className="ca-send" onClick={submit} disabled={!text.trim()} aria-label="Send"><ArrowUp size={18} /></button>}
      </div>
      <p className="ca-disclaimer">Answers use official university records and policies. Please confirm important decisions with the relevant office.</p>
    </div>
  )
}
