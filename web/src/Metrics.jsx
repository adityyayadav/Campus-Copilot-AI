import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getJSON } from './api.js'

const fmt = (v, s = '') => (v == null ? '-' : `${v}${s}`)

export default function Metrics() {
  const [m, setM] = useState(null)
  const [err, setErr] = useState(null)
  const load = () => getJSON('/api/metrics').then((x) => { setM(x); setErr(null) }).catch((e) => setErr(e.message))
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [])

  if (err) return <div className="metrics"><div className="alert warn">{err}</div></div>
  if (!m) return <div className="metrics"><p className="muted">Loading…</p></div>
  const maxRoute = Math.max(1, ...m.routes.map((r) => r.n))
  return (
    <div className="metrics">
      <div className="metrics-head">
        <div>
          <h2>Operational insights</h2>
          <p className="muted">Live telemetry from <code>ops.request_log</code>: latency, cost and safety across all requests.</p>
        </div>
        <button className="ghost-btn" onClick={load}><RefreshCw size={14} /> Refresh</button>
      </div>

      <div className="kpis">
        <Kpi v={m.requests} l="Requests" />
        <Kpi v={fmt(m.p50_ms != null ? (m.p50_ms / 1000).toFixed(1) : null, 's')} l="Median latency" sub={`p95 ${fmt(m.p95_ms != null ? (m.p95_ms / 1000).toFixed(1) : null, 's')}`} />
        <Kpi v={fmt(m.cache_hit_rate, '%')} l="Semantic cache hits" sub={`cached p50 ${fmt(m.p50_cached_ms, ' ms')}`} />
        <Kpi v={fmt(m.fast_route_rate, '%')} l="Routed without LLM" sub="tier-1 k-NN router" />
        <Kpi v={fmt(m.avg_tokens)} l="Avg tokens / answer" sub={`${fmt(m.avg_llm_calls)} LLM calls`} />
        <Kpi v={fmt(m.sql_success_rate, '%')} l="SQL success" />
        <Kpi v={fmt(m.grounded_rate, '%')} l="Grounded first time" />
        <Kpi v={m.blocked} l="Blocked by guardrails" />
      </div>

      <div className="metrics-grid">
        <div className="card">
          <div className="card-title">Route distribution</div>
          {m.routes.map((r) => (
            <div key={r.k} className="vote">
              <span className="vote-l wide">{r.k}</span>
              <div className="bar"><div className="bar-fill accent" style={{ width: `${(r.n / maxRoute) * 100}%` }} /></div>
              <span className="vote-v">{r.n}</span>
            </div>
          ))}
          {m.blocked_by_type.length > 0 && (<>
            <div className="card-title" style={{ marginTop: 18 }}>Blocked by type</div>
            {m.blocked_by_type.map((b) => <div key={b.k} className="kv"><span className="k">{b.k}</span><span className="v">{b.n}</span></div>)}
          </>)}
        </div>
        <div className="card">
          <div className="card-title">Recent requests</div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>time</th><th>route</th><th>router</th><th>cache</th><th>latency</th><th>tokens</th></tr></thead>
              <tbody>
                {m.recent.map((r, i) => (
                  <tr key={i}>
                    <td>{new Date(r.ts).toLocaleTimeString()}</td>
                    <td>{r.blocked ? <span className="chip danger">{r.blocked}</span> : `${r.route || '-'}${r.strategy ? ' · ' + r.strategy : ''}`}</td>
                    <td>{r.router_tier || '-'}</td>
                    <td>{r.cache_hit ? 'hit' : '-'}</td>
                    <td>{r.latency_ms != null ? `${(r.latency_ms / 1000).toFixed(1)}s` : '-'}</td>
                    <td>{r.tokens || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

function Kpi({ v, l, sub }) {
  return <div className="kpi"><div className="kpi-v">{v}</div><div className="kpi-l">{l}</div>{sub && <div className="kpi-s">{sub}</div>}</div>
}
