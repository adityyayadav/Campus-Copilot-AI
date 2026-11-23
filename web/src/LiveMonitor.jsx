import { useEffect, useMemo, useReducer, useState } from 'react'
import { Activity, Ban, Check, ChevronDown, Database, FileText, Loader2, Route, ShieldCheck, Sparkles, Zap } from 'lucide-react'

const STAGES = [
  { key: 'guard', label: 'Guardrails', icon: ShieldCheck },
  { key: 'cache', label: 'Cache', icon: Zap },
  { key: 'router', label: 'Router', icon: Route },
  { key: 'docs', label: 'Retrieval', icon: FileText },
  { key: 'sql', label: 'SQL agent', icon: Database },
  { key: 'synthesize', label: 'Synthesis', icon: Sparkles },
  { key: 'grounding', label: 'Verification', icon: Check },
]

function reducer(state, ev) {
  if (ev.type === 'reset') return {}
  const prev = state[ev.id] || { id: ev.id, ts: ev.ts, stages: {}, status: 'running' }
  const next = { ...prev, stages: { ...prev.stages } }
  if (ev.stage === 'start') Object.assign(next, { ts: ev.ts, question: ev.question, role: ev.role, user: ev.user })
  else next.stages[ev.stage] = ev
  if (ev.stage === 'final') next.status = 'done'
  if (ev.stage === 'guard' && ev.status === 'blocked') next.status = 'blocked'
  if (ev.stage === 'error') next.status = 'error'
  next.updated = ev.ts
  return { ...state, [ev.id]: next }
}

function stageState(req, key, index) {
  const s = req.stages[key]
  if (s) return s.status === 'blocked' ? 'blocked' : s.status === 'error' ? 'error' : 'done'
  if (req.status !== 'running') return 'skipped'
  const keys = STAGES.map((x) => x.key)
  const lastDone = Math.max(-1, ...keys.map((k, i) => (req.stages[k] ? i : -1)))
  // cache hit / out of scope jump straight to the end
  if (req.stages.cache?.status === 'hit' || req.stages.out_of_scope) return 'skipped'
  const route = req.stages.router?.route
  if (key === 'sql' && route === 'VECTOR_ONLY') return 'skipped'
  if (key === 'docs' && route === 'SQL_ONLY') return 'skipped'
  if (index === lastDone + 1 || (lastDone >= 2 && (key === 'docs' || key === 'sql') && !req.stages.synthesize)) return 'active'
  return 'pending'
}

const fmtMs = (ms) => (ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)
const time = (ts) => new Date(ts * 1000).toLocaleTimeString()

export default function LiveMonitor() {
  const [reqs, dispatch] = useReducer(reducer, {})
  const [conn, setConn] = useState('connecting')
  const [open, setOpen] = useState({})

  useEffect(() => {
    const es = new EventSource('/api/live')
    es.onopen = () => setConn('live')
    es.onerror = () => setConn('reconnecting')
    es.onmessage = (m) => { try { dispatch(JSON.parse(m.data)) } catch { /* ignore malformed */ } }
    return () => es.close()
  }, [])

  const list = useMemo(() => Object.values(reqs).filter((r) => r.question || Object.keys(r.stages).length)
    .sort((a, b) => b.ts - a.ts).slice(0, 40), [reqs])
  const running = list.filter((r) => r.status === 'running').length
  const newest = list[0]?.id

  return (
    <div className="live">
      <div className="live-head">
        <div>
          <h2><Activity size={20} /> Live pipeline</h2>
          <p className="muted">Every question asked anywhere in the app, as it moves through the pipeline. Open the assistant in another tab and ask something.</p>
        </div>
        <div className="live-badges">
          <span className={`live-dot ${conn === 'live' ? 'on' : conn}`} />{conn === 'live' ? 'Connected' : conn === 'reconnecting' ? 'Reconnecting…' : 'Connecting…'}
          <span className="chip subtle">{list.length} requests</span>
          <span className="chip subtle">{running} running</span>
        </div>
      </div>

      {list.length === 0 && (
        <div className="live-empty">
          <Activity size={22} />
          <p>Waiting for questions. Ask something in the <a href="/" target="_blank" rel="noreferrer">assistant</a> or the Assistant tab, and it will appear here step by step.</p>
        </div>
      )}

      <div className="live-list">
        {list.map((r) => {
          const expanded = open[r.id] ?? r.id === newest
          const fin = r.stages.final
          return (
            <div key={r.id} className={`live-card ${r.status}`}>
              <button className="live-card-head" onClick={() => setOpen((o) => ({ ...o, [r.id]: !expanded }))}>
                <span className="live-time">{r.ts ? time(r.ts) : ''}</span>
                <span className="chip subtle">{r.role || '—'}{r.user && r.role !== 'admin' ? ` · ${r.user}` : ''}</span>
                <span className="live-q">{r.question || '(question)'}</span>
                {r.status === 'running' && <span className="chip"><Loader2 size={12} className="spin" /> running</span>}
                {r.status === 'done' && <span className="chip ok"><Check size={12} /> {fmtMs(fin?.latency_ms)}{fin?.cache_hit ? ' · cached' : ''}</span>}
                {r.status === 'blocked' && <span className="chip danger"><Ban size={12} /> blocked</span>}
                {r.status === 'error' && <span className="chip warn">error</span>}
                <ChevronDown size={15} className={`live-caret ${expanded ? 'rot' : ''}`} />
              </button>

              <div className="live-track">
                {STAGES.map((st, i) => {
                  const state = stageState(r, st.key, i)
                  const s = r.stages[st.key]
                  const Icon = st.icon
                  const note = st.key === 'cache' && s ? s.status : st.key === 'router' && s ? (s.in_scope === false ? 'out of scope' : `${s.route}${s.strategy ? ' · ' + s.strategy : ''}`)
                    : st.key === 'grounding' && s ? s.status : st.key === 'guard' && s?.status === 'blocked' ? s.code : ''
                  return (
                    <div key={st.key} className={`live-stage ${state}`}>
                      <div className="live-stage-top">
                        {state === 'active' ? <Loader2 size={13} className="spin" /> : state === 'blocked' ? <Ban size={13} /> : <Icon size={13} />}
                        <span>{st.label}</span>
                      </div>
                      <div className="live-stage-sub">{note || (state === 'skipped' ? 'skipped' : '')}{s?.ms ? ` · ${fmtMs(s.ms)}` : ''}</div>
                    </div>
                  )
                })}
              </div>

              {expanded && <Details r={r} />}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Details({ r }) {
  const { guard, cache, router, docs, sql, grounding, final } = r.stages
  return (
    <div className="live-details">
      <div className="live-box">
        <div className="card-title">Guardrails & cache</div>
        <div className="kv"><span className="k">Injection score</span><span className="v mono">{guard?.score != null ? guard.score : '—'}</span></div>
        <div className="kv"><span className="k">Outcome</span><span className="v">{guard?.status === 'blocked' ? `blocked (${guard.code})` : guard ? 'passed' : '—'}</span></div>
        <div className="kv"><span className="k">Semantic cache</span><span className="v">{cache ? `${cache.status}${cache.similarity ? ` · ${cache.similarity}` : ''}` : '—'}</span></div>
        {final && <div className="kv"><span className="k">Cost</span><span className="v mono">{final.llm_calls ?? 0} LLM calls · {final.tokens ?? 0} tokens</span></div>}
      </div>

      {router && (
        <div className="live-box">
          <div className="card-title">Routing decision</div>
          <div className="kv"><span className="k">Route</span><span className="v">{router.in_scope === false ? 'out of scope' : `${router.route}${router.strategy ? ' · ' + router.strategy : ''}`}</span></div>
          <div className="kv"><span className="k">Decided by</span><span className="v">{router.tier === 'fast' ? 'Tier 1 · k-NN (no LLM)' : router.tier === 'llm' ? 'Tier 2 · LLM router' : 'Fallback'} · {Math.round((router.confidence || 0) * 100)}%</span></div>
          {router.knn && Object.entries(router.knn).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <div key={k} className="vote"><span className="vote-l">{k}</span><div className="bar"><div className="bar-fill accent" style={{ width: `${v * 100}%` }} /></div><span className="vote-v">{Math.round(v * 100)}%</span></div>
          ))}
          {router.reasoning && <p className="reason">“{router.reasoning}”</p>}
        </div>
      )}

      {docs && (
        <div className="live-box">
          <div className="card-title">Retrieval · {docs.reranked ? 'reranked' : 'fused'}{docs.relaxed ? ' · filter relaxed' : ''}</div>
          {(docs.sources || []).map((s, i) => (
            <div key={i} className="live-src">
              <span className="src-id">D{i + 1}</span>
              <span className="live-src-t">{s.title}<span className="muted"> › {s.section}{s.page ? ` · p.${s.page}` : ''}</span></span>
              {s.score != null && <span className="vote-v">{Number(s.score).toFixed(2)}</span>}
            </div>
          ))}
          {docs.timings && <div className="muted small mono">{Object.entries(docs.timings).filter(([, v]) => typeof v === 'number').map(([k, v]) => `${k.replace('_ms', '')} ${v}ms`).join(' · ')}</div>}
        </div>
      )}

      {sql && (
        <div className="live-box wide">
          <div className="card-title">SQL agent · {sql.rows} rows · {sql.attempts} attempt{sql.attempts > 1 ? 's' : ''}{sql.used_policy ? ' · policy rules applied' : ''}</div>
          {sql.sql && <pre className="code">{sql.sql}</pre>}
          {sql.error && <div className="alert warn small">{sql.error}</div>}
        </div>
      )}

      {grounding && (
        <div className="live-box">
          <div className="card-title">Verification</div>
          <div className="kv"><span className="k">Status</span><span className="v">{grounding.status}</span></div>
          <div className="kv"><span className="k">Figures checked</span><span className="v">{grounding.checked ?? '—'}</span></div>
          {grounding.unsupported?.length > 0 && <div className="kv"><span className="k">Revised</span><span className="v mono">{grounding.unsupported.join(', ')}</span></div>}
        </div>
      )}
    </div>
  )
}
