export async function getJSON(path) {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`)
  return r.json()
}

/** POST /api/chat and invoke onEvent for every Server-Sent Event. */
export async function streamChat(body, onEvent, signal) {
  const r = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!r.ok) {
    let msg = `Request failed (HTTP ${r.status})`
    try {
      const j = await r.json()
      if (j.detail) msg = Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join('; ') : String(j.detail)
    } catch { /* ignore */ }
    onEvent({ type: 'error', message: msg })
    return
  }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      const line = chunk.split('\n').find((l) => l.startsWith('data: '))
      if (line) {
        try { onEvent(JSON.parse(line.slice(6))) } catch { /* malformed event */ }
      }
    }
  }
}
