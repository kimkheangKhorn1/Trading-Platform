import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// A mouse click should not leave the browser's focus ring on toolbar buttons: pressing a key later
// (Esc, Alt+R, Shift+→ ...) makes the browser show it on whatever button was clicked last.
// Keyboard activation (click with detail 0) keeps focus, so keyboard users still see where they are.
document.addEventListener('click', (e) => {
  if (e.detail === 0) return
  const button = (e.target as Element | null)?.closest?.('.topbar button, .replay-bar button, .chart-footer button, .legend button, .tester button')
  if (button instanceof HTMLElement && !button.matches('[aria-haspopup], [role=combobox]')) button.blur()
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
