import React from 'react'
import { createRoot } from 'react-dom/client'

// Student-facing assistant. Loaded as a separate chunk so its CSS ships with it.
import('./assistant/Assistant.jsx').then(({ default: Root }) => {
  createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <Root />
    </React.StrictMode>,
  )
})
