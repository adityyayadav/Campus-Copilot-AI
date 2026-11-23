import { useState } from 'react'
import { Coins, Database, FileText, Route, ShieldCheck } from 'lucide-react'
import { RouteChip } from './Chat.jsx'

const TABS = [
  { key: 'route', label: 'Routing', icon: Route },
  { key: 'sql', label: 'SQL', icon: Database },
  { key: 'docs', label: 'Sources', icon: FileText },
  { key: 'trust', label: 'Trust', icon: ShieldCheck },
  { key: 'cost', label: 'Cost', icon: Coins },
]

export default function Inspector({ m }) {
  const [tab, setTab] = useState('route')
  const d = m.data || {}
  return (
    <div className="insp">
      <div className="insp-title">How this answer was built</div>
      <div className="insp-tabs">
        {TABS.map((t) => {
          const Icon = t.icon
          const disabled = (t.key === 'sql' && !d.sql) || (t.key === 'docs' && !d.docs)
          return (
            <button key={t.key} disabled={disabled} className={`insp-tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>
              <Icon size={13} />{t.label}
            </button>
          )
        })}
      </div>
      <div className="insp-body">
        {m.status === 'blocked' ? <Blocked m={m} /> :
          tab === 'route' ? <RouteTab d={d} /> :
          tab === 'sql' ? <SqlTab s={d.sql} /> :
          tab === 'docs' ? <DocsTab docs={d.docs} /> :
          tab === 'trust' ? <TrustTab m={m} d={d} /> : <CostTab d={d} />}
      </div>
    </div>
  )
}

function KV({ k, v, mono }) {
  return <div className="kv"><span className="k">{k}</span><span className={`v ${mono ? 'mono' : ''}`}>{v ?? '-'}</span></div>
}

function Bar({ value, max = 1, cls = '' }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100))
  return <div className="bar"><div className={`bar-fill ${cls}`} style={{ width: `${pct}%` }} /></div>
}

function Blocked({ m }) {
  const det = m.blocked?.detail || {}
  return (
    <div className="card">
      <div className="card-title">Guardrail decision</div>
      <KV k="Outcome" v={<span className="chip danger">blocked · {m.blocked?.code}</span>} />
      {det.classifier_score != null && <KV k="Prompt Guard 2 score" v={det.classifier_score} mono />}
      {det.heuristic_match && <KV k="Heuristic match" v={`“${det.heuristic_match}”`} mono />}
      {det.requested && <KV k="Requested records" v={det.requested.join(', ')} mono />}
      {det.signed_in_as && <KV k="Signed in as" v={det.signed_in_as} mono />}
      <p className="muted small">The request never reached the router, the database or the LLM, so no tokens were spent.</p>
    </div>
  )
}

function RouteTab({ d }) {
  const r = d.router || {}
  const fast = r.fast || {}
  if (!d.route) return <p className="muted">Routing…</p>
  const votes = Object.entries(fast.votes || {}).sort((a, b) => b[1] - a[1])
  return (
    <>
      <div className="card">
        <div className="card-title">Decision</div>
        <div className="route-big">
          {r.in_scope === false ? <span className="chip subtle">out of scope</span> : <RouteChip route={d.route} strategy={r.strategy} />}
        </div>
        <KV k="Decided by" v={r.tier === 'fast' ? 'Tier 1 · embedding k-NN (no LLM call)' : r.tier === 'llm' ? `Tier 2 · LLM router (${(r.model || '').replace('openai/', '')})` : 'Fallback policy'} />
        <KV k="Confidence" v={`${Math.round((r.confidence || 0) * 100)}%`} />
        <KV k="Routing time" v={`${r.ms ?? '-'} ms`} />
        {r.reasoning && <p className="reason">“{r.reasoning}”</p>}
      </div>
      {votes.length > 0 && (
        <div className="card">
          <div className="card-title">Tier 1 · k-NN classifier <span className="muted small">({fast.ms} ms)</span></div>
          {votes.map(([label, v]) => (
            <div key={label} className="vote"><span className="vote-l">{label}</span><Bar value={v} cls={label === fast.label ? 'accent' : ''} /><span className="vote-v">{Math.round(v * 100)}%</span></div>
          ))}
          <p className="muted small">Accepted without an LLM call when confidence ≥ 75% and no decomposition is needed.</p>
        </div>
      )}
      {(r.sql_question || r.doc_question) && (
        <div className="card">
          <div className="card-title">Decomposition</div>
          {r.doc_question && <div className="subq"><span className="chip vec">docs</span>{r.doc_question}</div>}
          {r.sql_question && <div className="subq"><span className="chip sql">sql</span>{r.sql_question}</div>}
          {r.doc_topic && <KV k="Metadata filter" v={`topic = ${r.doc_topic}`} mono />}
        </div>
      )}
    </>
  )
}

function SqlTab({ s }) {
  if (!s) return <p className="muted">The database was not queried.</p>
  return (
    <>
      <div className="card">
        <div className="card-title">Generated query</div>
        <div className="tags">
          {s.tables?.map((t) => <span key={t} className="tag">{t}</span>)}
          {s.used_policy && <span className="tag accent">policy rules injected</span>}
        </div>
        <pre className="code">{s.sql || '-'}</pre>
        <KV k="Attempts" v={`${s.attempts}${s.attempts > 1 ? ' (self-repaired)' : ''}`} />
        <KV k="Execution" v="read-only role · row-level security · 5 s timeout" />
        <KV k="Time" v={`${s.ms} ms`} />
        {s.error && <div className="alert warn small">{s.error}</div>}
        {s.steps?.filter((x) => x.error).map((x, i) => <div key={i} className="repair">↻ {x.error}</div>)}
      </div>
      {s.rows?.length > 0 && (
        <div className="card">
          <div className="card-title">Result · {s.rows.length} row{s.rows.length > 1 ? 's' : ''}</div>
          <div className="table-wrap">
            <table>
              <thead><tr>{s.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{s.rows.slice(0, 50).map((r, i) => <tr key={i}>{s.columns.map((c) => <td key={c}>{String(r[c] ?? '')}</td>)}</tr>)}</tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}

function DocsTab({ docs }) {
  if (!docs) return <p className="muted">Documents were not searched.</p>
  return (
    <>
      <div className="card">
        <div className="card-title">Retrieval pipeline</div>
        <KV k="Query" v={docs.query} />
        <KV k="Stages" v="pgvector (dense) + BM25 (SQL) → RRF → rerank" />
        <KV k="Filter" v={Object.keys(docs.filters || {}).length ? JSON.stringify(docs.filters) : docs.filter_relaxed ? 'relaxed (no relevant hit)' : 'none'} mono />
        <KV k="Reranked" v={docs.reranked ? 'yes · Cohere v3.5' : 'no'} />
        <KV k="Timings" v={Object.entries(docs.timings || {}).map(([k, v]) => `${k.replace('_ms', '')} ${v}`).join(' · ')} mono />
        {docs.hyde && <p className="reason">HyDE: “{docs.hyde}”</p>}
      </div>
      {docs.chunks.map((c, i) => (
        <div key={i} className="card source">
          <div className="source-head">
            <span className="src-id">D{i + 1}</span>
            <div className="src-title"><b>{c.title}</b><span>{c.section}</span></div>
            <span className={`tag ${c.collection === 'table_rows' ? 'accent' : ''}`}>{c.collection === 'table_rows' ? 'table row' : 'document'}</span>
          </div>
          <div className="scores">
            {c.rerank_score != null && <span>rerank <b>{c.rerank_score.toFixed(3)}</b></span>}
            {c.dense_rank != null && <span>dense #{c.dense_rank}</span>}
            {c.bm25_rank != null && <span>bm25 #{c.bm25_rank}</span>}
            <span>rrf {c.rrf}</span>
          </div>
          {c.rerank_score != null && <Bar value={c.rerank_score} cls="accent" />}
          <p className="src-text">{c.content}</p>
        </div>
      ))}
    </>
  )
}

function TrustTab({ m, d }) {
  const g = d.grounding || {}
  const inj = m.guard || {}
  return (
    <>
      <div className="card">
        <div className="card-title">Input guardrails</div>
        <KV k="Prompt injection (Prompt Guard 2)" v={inj.classifier_score != null ? `${(inj.classifier_score * 100).toFixed(2)}% risk` : 'n/a'} />
        <KV k="Heuristic scan" v={inj.heuristic_match ? 'match' : 'clean'} />
        <KV k="Authorization" v="own records only · RLS enforced in Postgres" />
      </div>
      <div className="card">
        <div className="card-title">Answer grounding</div>
        <KV k="Status" v={g.status === 'grounded' ? <span className="chip ok">all figures traced to evidence</span> : g.status === 'revised' ? <span className="chip warn">revised once</span> : g.status || '-'} />
        <KV k="Figures checked" v={g.checked_numbers} />
        {g.unsupported?.length > 0 && <KV k="Flagged in draft" v={g.unsupported.join(', ')} mono />}
        {g.derived_after_revision?.length > 0 && <KV k="Derived (calculated) values" v={g.derived_after_revision.join(', ')} mono />}
        <p className="muted small">Every number in the answer must appear in the SQL rows or the cited policy text; anything else is sent back to the model to show its calculation or remove it.</p>
      </div>
      <div className="card">
        <div className="card-title">Semantic cache</div>
        <KV k="Result" v={d.cache?.hit ? `hit · similarity ${d.cache.similarity}` : 'miss → stored if verified'} />
        {d.cache?.matched_question && <KV k="Matched" v={d.cache.matched_question} />}
        <p className="muted small">Scoped per user, exact-entity match, 5 min TTL for live data, 24 h for policy answers.</p>
      </div>
    </>
  )
}

function CostTab({ d }) {
  const u = d.usage || {}
  const trace = d.trace || []
  const max = Math.max(1, ...trace.map((t) => t.ms || 0))
  return (
    <>
      <div className="stat-grid">
        <div className="stat"><div className="stat-v">{d.latency_ms != null ? (d.latency_ms / 1000).toFixed(2) + 's' : '-'}</div><div className="stat-l">end-to-end</div></div>
        <div className="stat"><div className="stat-v">{u.llm_calls ?? '-'}</div><div className="stat-l">LLM calls</div></div>
        <div className="stat"><div className="stat-v">{u.tokens != null ? u.tokens.toLocaleString() : '-'}</div><div className="stat-l">tokens</div></div>
        <div className="stat"><div className="stat-v">{u.embed_api_calls ?? '-'}</div><div className="stat-l">embedding calls</div></div>
      </div>
      <div className="card">
        <div className="card-title">Timeline</div>
        {trace.map((t, i) => (
          <div key={i} className="vote"><span className="vote-l">{t.node}</span><Bar value={t.ms || 0} max={max} cls="accent" /><span className="vote-v">{t.ms} ms</span></div>
        ))}
        {d.cache?.hit && <p className="muted small">Served from the semantic cache: no LLM calls.</p>}
      </div>
      {u.models && Object.keys(u.models).length > 0 && (
        <div className="card">
          <div className="card-title">Models used</div>
          {Object.entries(u.models).map(([k, v]) => <KV key={k} k={k} v={`${v} call${v > 1 ? 's' : ''}`} mono />)}
        </div>
      )}
    </>
  )
}
