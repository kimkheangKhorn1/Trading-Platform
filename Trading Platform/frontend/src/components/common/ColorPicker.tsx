import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { hexToHsv, hsvToHex, joinColor, splitColor } from '../../lib/colors'

/** TradingView's palette: a gray ramp, the main hues, then lighter and darker variants of each hue. */
const PALETTE = [
  ['#FFFFFF', '#D1D4DC', '#B2B5BE', '#9598A1', '#787B86', '#5D606B', '#434651', '#2A2E39', '#131722', '#000000'],
  ['#F23645', '#FF9800', '#FFEB3B', '#4CAF50', '#089981', '#00BCD4', '#2962FF', '#673AB7', '#9C27B0', '#E91E63'],
  ['#FCCBCD', '#FFE0B2', '#FFF9C4', '#C8E6C9', '#ACE5DC', '#B2EBF2', '#BBD9FB', '#D1C4E9', '#E1BEE7', '#F8BBD0'],
  ['#FAA1A4', '#FFCC80', '#FFF59D', '#A5D6A7', '#70CCBD', '#80DEEA', '#90BFF9', '#B39DDB', '#CE93D8', '#F48FB1'],
  ['#F7525F', '#FFB74D', '#FFF176', '#81C784', '#42BDA8', '#4DD0E1', '#5B9CF6', '#9575CD', '#BA68C8', '#F06292'],
  ['#B22833', '#F57C00', '#FBC02D', '#388E3C', '#056656', '#0097A7', '#1848CC', '#512DA8', '#7B1FA2', '#C2185B'],
  ['#801922', '#E65100', '#F57F17', '#1B5E20', '#00332A', '#006064', '#0C3299', '#311B92', '#4A148C', '#880E4F'],
]
const POP_WIDTH = 258

/** Calls onMove with the pointer position as 0-1 fractions of the element, while dragging. */
function drag(e: ReactPointerEvent<HTMLElement>, onMove: (x: number, y: number) => void) {
  const el = e.currentTarget
  const move = (ev: { clientX: number; clientY: number }) => {
    const r = el.getBoundingClientRect()
    onMove(Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)))
  }
  el.setPointerCapture(e.pointerId)
  move(e)
  const up = () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up) }
  el.addEventListener('pointermove', move)
  el.addEventListener('pointerup', up)
}

/** TradingView-style color swatch: palette, custom color (any hex) and opacity. Value is #RRGGBBAA. */
export function ColorField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false)
  const [custom, setCustom] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ left: 0, top: 0 })
  const { hex, alpha } = splitColor(value)
  // hue/saturation survive while dragging through gray or black, where the hex alone loses them
  const [hsv, setHsv] = useState(() => hexToHsv(hex))
  if (hsvToHex(hsv.h, hsv.s, hsv.v) !== hex.toUpperCase()) setHsv(hexToHsv(hex))
  const [hexText, setHexText] = useState(hex.slice(1).toUpperCase())
  const [seenHex, setSeenHex] = useState(hex)
  if (seenHex !== hex) { setSeenHex(hex); setHexText(hex.slice(1).toUpperCase()) }

  const pct = Math.round(alpha * 100)
  const setHex = (h: string) => onChange(joinColor(h, alpha))
  const setHsvColor = (next: { h: number; s: number; v: number }) => { setHsv(next); setHex(hsvToHex(next.h, next.s, next.v)) }

  useLayoutEffect(() => {
    if (!open || !trigger.current || !pop.current) return
    const place = () => {
      const r = trigger.current!.getBoundingClientRect()
      const h = pop.current!.offsetHeight
      const below = window.innerHeight - r.bottom - 12
      const top = below >= h || below >= r.top ? r.bottom + 4 : Math.max(8, r.top - 4 - h)
      setPos({ left: Math.max(8, Math.min(r.right - POP_WIDTH, window.innerWidth - POP_WIDTH - 8)), top })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open, custom])

  useEffect(() => {
    if (!open) return
    const outside = (e: PointerEvent) => {
      if (!trigger.current?.contains(e.target as Node) && !pop.current?.contains(e.target as Node)) setOpen(false)
    }
    const scroll = (e: Event) => { if (!pop.current?.contains(e.target as Node)) setOpen(false) }
    // capture phase so Escape closes only the picker, not the settings dialog behind it
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      setOpen(false)
      trigger.current?.focus()
    }
    document.addEventListener('pointerdown', outside)
    window.addEventListener('scroll', scroll, true)
    window.addEventListener('keydown', key, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      window.removeEventListener('scroll', scroll, true)
      window.removeEventListener('keydown', key, true)
    }
  }, [open])

  const swatchColor = `${hex}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`
  return (
    <span className="color-field">
      <button ref={trigger} className="swatch" style={{ ['--c' as string]: swatchColor }}
        onClick={() => setOpen(!open)} aria-label="Pick color" aria-expanded={open} />
      {open ? createPortal(
        <div ref={pop} className="color-pop" style={{ left: pos.left, top: pos.top, width: POP_WIDTH }} role="dialog" aria-label="Color picker">
          <div className="palette" role="listbox" aria-label="Palette">
            {PALETTE.flat().map((c) => (
              <button key={c} className={`pal ${c === hex.toUpperCase() ? 'sel' : ''}`} style={{ background: c }}
                title={c} aria-label={c} role="option" aria-selected={c === hex.toUpperCase()} onClick={() => setHex(c)} />
            ))}
          </div>
          <div className="pop-sep" />
          <button className={`custom-toggle ${custom ? 'on' : ''}`} onClick={() => setCustom(!custom)} aria-expanded={custom}>
            <span className="plus" aria-hidden>{custom ? '−' : '+'}</span> Custom color
          </button>
          {custom ? (
            <div className="custom">
              <div className="sv" style={{ background: `hsl(${hsv.h} 100% 50%)` }} role="slider" aria-label="Saturation and brightness"
                aria-valuetext={hex} tabIndex={0}
                onPointerDown={(e) => drag(e, (x, y) => setHsvColor({ ...hsv, s: x, v: 1 - y }))}>
                <span className="sv-dot" style={{ left: `${hsv.s * 100}%`, top: `${(1 - hsv.v) * 100}%`, background: hex }} />
              </div>
              <div className="hue" role="slider" aria-label="Hue" aria-valuemin={0} aria-valuemax={360} aria-valuenow={Math.round(hsv.h)}
                tabIndex={0} onPointerDown={(e) => drag(e, (x) => setHsvColor({ ...hsv, h: Math.min(359.9, x * 360) }))}
                onKeyDown={(e) => {
                  const d = e.key === 'ArrowRight' ? 5 : e.key === 'ArrowLeft' ? -5 : 0
                  if (d) { e.preventDefault(); setHsvColor({ ...hsv, h: (hsv.h + d + 360) % 360 }) }
                }}>
                <span className="hue-dot" style={{ left: `${(hsv.h / 360) * 100}%` }} />
              </div>
              <label className="hex-row">
                <span className="preview" style={{ ['--c' as string]: swatchColor }} />
                <span className="muted">#</span>
                <input className="input mono" value={hexText} maxLength={6} spellCheck={false} aria-label="Hex color"
                  onChange={(e) => {
                    const t = e.target.value.replace(/[^0-9a-fA-F]/g, '').toUpperCase()
                    setHexText(t)
                    if (t.length === 6) setHex(`#${t}`)
                  }}
                  onBlur={() => setHexText(hex.slice(1).toUpperCase())} />
              </label>
            </div>
          ) : null}
          <div className="pop-sep" />
          <div className="opacity">
            <span>Opacity</span>
            <div className="opacity-track" style={{ ['--c' as string]: hex }}>
              <input type="range" min={0} max={100} value={pct} aria-label="Opacity"
                onChange={(e) => onChange(joinColor(hex, Number(e.target.value) / 100))} />
            </div>
            <input className="input mono pct" type="number" min={0} max={100} value={pct} aria-label="Opacity percent"
              onChange={(e) => {
                const n = Number(e.target.value)
                if (e.target.value !== '' && Number.isFinite(n)) onChange(joinColor(hex, Math.min(100, Math.max(0, n)) / 100))
              }} />
            <span className="muted">%</span>
          </div>
        </div>,
        document.body,
      ) : null}
    </span>
  )
}
