import { useEffect, useRef, useState } from 'react'
import { Activity, BarChart3, GraduationCap, MessageSquare, PanelRightClose, PanelRightOpen, Plus, Sparkles } from 'lucide-react'
import LiveMonitor from './LiveMonitor.jsx'
import { getJSON, streamChat } from './api.js'
import { Composer, EmptyState, MessageView } from './Chat.jsx'
import Inspector from './Inspector.jsx'
import Metrics from './Metrics.jsx'
import Sidebar from './Sidebar.jsx'

let nextId = 1

export default function App() {
  const [view, setView] = useState('chat')
  const [personas, setPersonas] = useState([])
  const [studentId, setStudentId] = useState('S1023')
  const [opts, setOpts] = useState({ use_rerank: true, use_hyde: false })
  const [health, setHealth] = useState(null)
  const [messages, setMessages] = useState([])
  const [selected, setSelected] = useState(null)
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [busy, setBusy] = useState(false)
  const scroller = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => {
    getJSON('/api/personas').then(setPersonas).catch(() => setPersonas([]))
    getJSON('/api/health').then(setHealth).catch(() => setHealth({ db: 'unreachable' }))
  }, [])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  const profile = personas.find((p) => p.student_id === studentId) || null

  function patch(id, fn) {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)))
  }

  async function ask(question) {
    if (!question.trim() || busy) return
    const history = messages
      .filter((m) => m.status !== 'blocked' && m.status !== 'error')
      .map((m) => ({ role: m.role, content: m.role === 'user' ? m.content : m.answer || '' }))
      .filter((m) => m.content)
      .slice(-6)
      .map((m) => ({ ...m, content: m.content.slice(0, 1500) }))
    const uid = nextId++
    const aid = nextId++
    setMessages((ms) => [
      ...ms,
      { id: uid, role: 'user', content: question },
      { id: aid, role: 'assistant', status: 'streaming', steps: {}, data: null, answer: '', started: Date.now() },
    ])
    setSelected(aid)
    setBusy(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await streamChat(
        { question, student_id: studentId || null, history, ...opts },
        (ev) => {
          if (ev.type === 'guard') patch(aid, (m) => ({ steps: { ...m.steps, guard: 'done' }, guard: ev.injection }))
          else if (ev.type === 'cache')
            patch(aid, (m) => ({ steps: { ...m.steps, cache: ev.hit ? 'hit' : 'miss' } }))
          else if (ev.type === 'step')
            patch(aid, (m) => ({ steps: { ...m.steps, [ev.node]: 'done' }, data: ev.data }))
          else if (ev.type === 'final')
            patch(aid, () => ({ status: 'done', data: ev, answer: ev.answer }))
          else if (ev.type === 'blocked')
            patch(aid, (m) => ({ status: 'blocked', steps: { ...m.steps, guard: 'blocked' }, blocked: ev, answer: ev.message }))
          else if (ev.type === 'error') patch(aid, () => ({ status: 'error', answer: ev.message }))
        },
        ctrl.signal,
      )
    } catch (e) {
      if (e.name !== 'AbortError') patch(aid, () => ({ status: 'error', answer: 'Could not reach the server.' }))
    } finally {
      setMessages((ms) => ms.map((m) => (m.id === aid && m.status === 'streaming' ? { ...m, status: 'error', answer: m.answer || 'The response was interrupted.' } : m)))
      setBusy(false)
    }
  }

  function newChat() {
    abortRef.current?.abort()
    setMessages([])
    setSelected(null)
  }

  const selectedMsg = messages.find((m) => m.id === selected)

  return (
    <div className={`app ${inspectorOpen && view === 'chat' ? 'with-inspector' : ''}`}>
      <Sidebar
        personas={personas}
        studentId={studentId}
        setStudentId={(id) => { setStudentId(id); newChat() }}
        opts={opts}
        setOpts={setOpts}
        health={health}
      />

      <main className="main">
        <header className="topbar">
          <div className="tabs">
            <button className={view === 'chat' ? 'tab active' : 'tab'} onClick={() => setView('chat')}>
              <MessageSquare size={15} /> Assistant
            </button>
            <button className={view === 'live' ? 'tab active' : 'tab'} onClick={() => setView('live')}>
              <Activity size={15} /> Live
            </button>
            <button className={view === 'metrics' ? 'tab active' : 'tab'} onClick={() => setView('metrics')}>
              <BarChart3 size={15} /> Insights
            </button>
          </div>
          <div className="topbar-right">
            {view === 'chat' && (
              <>
                <button className="ghost-btn" onClick={newChat}><Plus size={15} /> New chat</button>
                <button className="icon-btn" title="Toggle inspector" onClick={() => setInspectorOpen((v) => !v)}>
                  {inspectorOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
                </button>
              </>
            )}
          </div>
        </header>

        {view === 'chat' ? (
          <>
            <div className="scroll" ref={scroller}>
              {messages.length === 0 ? (
                <EmptyState profile={profile} onPick={ask} />
              ) : (
                <div className="thread">
                  {messages.map((m) => (
                    <MessageView key={m.id} m={m} selected={m.id === selected} onSelect={() => { setSelected(m.id); setInspectorOpen(true) }} />
                  ))}
                </div>
              )}
            </div>
            <Composer onSend={ask} busy={busy} profile={profile} />
          </>
        ) : view === 'live' ? (
          <div className="scroll"><LiveMonitor /></div>
        ) : (
          <div className="scroll"><Metrics /></div>
        )}
      </main>

      {view === 'chat' && inspectorOpen && (
        <aside className="inspector">
          {selectedMsg && selectedMsg.role === 'assistant' ? (
            <Inspector m={selectedMsg} />
          ) : (
            <div className="inspector-empty">
              <Sparkles size={22} />
              <p>Ask a question to see how the answer is built: the routing decision, the SQL, the retrieved sources, the safety checks and the cost.</p>
            </div>
          )}
        </aside>
      )}
      <div className="brand-mark"><GraduationCap size={14} /> Campus Copilot AI</div>
    </div>
  )
}
