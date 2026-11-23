import React from 'react'
import { createRoot } from 'react-dom/client'

// "/"         → student-facing assistant (clean, non-technical)
// "/console"  → developer console (routing, SQL, sources, cost, metrics)
//
// Keep the two dynamic imports as separate statements: Vite attaches each page's CSS to its own
// import() call, and a single `cond ? import(a) : import(b)` expression only preloads one page's CSS.
let load
if (window.location.pathname.startsWith('/console')) {
  load = import('./console.jsx')
} else {
  load = import('./assistant/Assistant.jsx')
}

load.then(({ default: Root }) => {
  createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <Root />
    </React.StrictMode>,
  )
})
