import { Database, GraduationCap, Layers, ShieldCheck, Sparkles, UserRound } from 'lucide-react'

export default function Sidebar({ personas, studentId, setStudentId, opts, setOpts, health }) {
  const ok = health?.db === 'ok'
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="logo"><GraduationCap size={20} /></div>
        <div>
          <div className="brand-name">Campus Copilot</div>
          <div className="brand-sub">Developer console · <a href="/" style={{ color: 'inherit' }}>open assistant</a></div>
        </div>
      </div>

      <div className="side-section">
        <div className="side-label">Signed in as</div>
        <div className="persona-list">
          <button className={`persona ${!studentId ? 'active' : ''}`} onClick={() => setStudentId('')}>
            <div className="avatar guest"><UserRound size={15} /></div>
            <div className="persona-text">
              <div className="persona-name">Guest</div>
              <div className="persona-meta">Public information only</div>
            </div>
          </button>
          {personas.map((p) => (
            <button key={p.student_id} className={`persona ${studentId === p.student_id ? 'active' : ''}`}
              onClick={() => setStudentId(p.student_id)}>
              <div className="avatar">{p.name.split(' ').map((s) => s[0]).join('')}</div>
              <div className="persona-text">
                <div className="persona-name">{p.name} <span className="sid">{p.student_id}</span></div>
                <div className="persona-meta">{p.role === 'admin' ? 'Admin · all records' : `${p.dept_id} · Year ${p.year} · GPA ${p.gpa.toFixed(2)} · ${p.attendance_pct}% att.`}</div>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="side-section">
        <div className="side-label">Retrieval</div>
        <Toggle label="Cross-encoder reranking" hint="Cohere Rerank v3.5" on={opts.use_rerank}
          set={(v) => setOpts({ ...opts, use_rerank: v })} />
        <Toggle label="HyDE query expansion" hint="Hypothetical answer for dense search" on={opts.use_hyde}
          set={(v) => setOpts({ ...opts, use_hyde: v })} />
      </div>

      <div className="side-section stack">
        <div className="side-label">System</div>
        <Stat icon={<Database size={14} />} label="PostgreSQL + pgvector" value={ok ? `${health.indexed_chunks} chunks` : health?.db || '…'} good={ok} />
        <Stat icon={<Sparkles size={14} />} label="LLM" value={health?.llm?.main?.replace('openai/', '') || '…'} good={health?.llm?.configured} />
        <Stat icon={<Layers size={14} />} label="Embeddings" value={health?.embeddings ? `${health.embeddings.provider} · ${health.embeddings.dim}d` : '…'} good={health?.embeddings?.configured} />
        <Stat icon={<ShieldCheck size={14} />} label="Guard" value="Prompt Guard 2" good={!!health} />
      </div>
    </aside>
  )
}

function Toggle({ label, hint, on, set }) {
  return (
    <button className="toggle-row" onClick={() => set(!on)}>
      <div>
        <div className="toggle-label">{label}</div>
        <div className="toggle-hint">{hint}</div>
      </div>
      <div className={`switch ${on ? 'on' : ''}`}><div className="knob" /></div>
    </button>
  )
}

function Stat({ icon, label, value, good }) {
  return (
    <div className="sys-stat">
      <span className={`dot ${good ? 'ok' : 'warn'}`} />
      {icon}
      <span className="sys-label">{label}</span>
      <span className="sys-value">{value}</span>
    </div>
  )
}
