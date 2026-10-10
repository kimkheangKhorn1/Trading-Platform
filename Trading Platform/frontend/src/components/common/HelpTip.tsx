import { useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'

const CARD_WIDTH = 320

/** ⓘ icon that shows a help card (what the setting does + an example) on hover or keyboard focus. */
export function HelpTip({ title, about, example }: { title: string; about: string; example?: string | null }) {
  const icon = useRef<HTMLButtonElement>(null)
  const card = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ left: 0, top: 0 })

  useLayoutEffect(() => {
    if (!open || !icon.current || !card.current) return
    const r = icon.current.getBoundingClientRect()
    const h = card.current.offsetHeight
    // prefer the left of the icon (the dialog's right edge is close), else below/above it
    const left = r.left - CARD_WIDTH - 8 >= 8 ? r.left - CARD_WIDTH - 8 : Math.max(8, Math.min(r.left, window.innerWidth - CARD_WIDTH - 8))
    const top = Math.max(8, Math.min(r.top - 10, window.innerHeight - h - 8))
    setPos({ left, top })
  }, [open])

  return (
    <>
      <button ref={icon} type="button" className="tip-icon" aria-label={`About ${title}`} aria-expanded={open}
        onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
        onKeyDown={(e) => { if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false) } }}>
        <Icon name="info" size={15} />
      </button>
      {open ? createPortal(
        <div ref={card} className="help-card" role="tooltip" style={{ left: pos.left, top: pos.top, width: CARD_WIDTH }}>
          <div className="help-title">{title}</div>
          <p className="help-about">{about}</p>
          {example ? (
            <div className="help-example">
              <span className="help-example-label">Example</span>
              <p>{example}</p>
            </div>
          ) : null}
        </div>,
        document.body,
      ) : null}
    </>
  )
}
