import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'

/** App-styled select: the menu lives outside scrolling dialogs so it cannot be clipped. */
export function Select<T extends string | number>({ value, options, onChange, width, ariaLabel, className = '' }: {
  value: T
  options: { value: T; label: string; disabled?: boolean }[]
  onChange: (v: T) => void
  width?: number
  ariaLabel?: string
  className?: string
}) {
  const id = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const search = useRef({ text: '', time: 0 })
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0, maxHeight: 252 })
  const selected = options.findIndex((o) => o.value === value)
  const enabled = options.flatMap((o, i) => o.disabled ? [] : [i])
  const activeIndex = enabled.includes(active) ? active : enabled[0] ?? -1

  const begin = (last = false) => {
    setActive(enabled.includes(selected) ? selected : enabled[last ? enabled.length - 1 : 0] ?? -1)
    search.current = { text: '', time: 0 }
    setOpen(true)
  }
  const choose = (index: number) => {
    const option = options[index]
    if (!option || option.disabled) return
    onChange(option.value)
    setOpen(false)
    trigger.current?.focus()
  }

  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return
    const place = () => {
      const rect = trigger.current!.getBoundingClientRect()
      const gap = 4
      const edge = 8
      const below = window.innerHeight - rect.bottom - gap - edge
      const above = rect.top - gap - edge
      const desired = Math.min(252, menu.current!.scrollHeight || options.length * 32 + 8)
      const upwards = below < desired && above > below
      const maxHeight = Math.max(0, Math.min(252, upwards ? above : below))
      const menuWidth = Math.min(Math.max(rect.width, menu.current!.scrollWidth), window.innerWidth - edge * 2)
      setPosition({
        left: Math.max(edge, Math.min(rect.left, window.innerWidth - menuWidth - edge)),
        top: upwards ? Math.max(edge, rect.top - gap - Math.min(desired, maxHeight)) : rect.bottom + gap,
        width: menuWidth, maxHeight,
      })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open, options])

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) setOpen(false)
    }
    const scroll = (event: Event) => {
      if (!menu.current?.contains(event.target as Node)) setOpen(false)
    }
    const blur = () => setOpen(false)
    document.addEventListener('pointerdown', outside)
    window.addEventListener('scroll', scroll, true)
    window.addEventListener('blur', blur)
    return () => {
      document.removeEventListener('pointerdown', outside)
      window.removeEventListener('scroll', scroll, true)
      window.removeEventListener('blur', blur)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const option = document.getElementById(`${id}-option-${activeIndex}`)
    if (option && menu.current) {
      const top = option.offsetTop
      const bottom = top + option.offsetHeight
      if (top < menu.current.scrollTop) menu.current.scrollTop = top
      else if (bottom > menu.current.scrollTop + menu.current.clientHeight) menu.current.scrollTop = bottom - menu.current.clientHeight
    }
  }, [open, activeIndex, id])

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const { key } = event
    if (key === 'Escape' && open) {
      // Close this menu without also cancelling the enclosing settings dialog.
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    } else if (key === 'Tab') {
      setOpen(false)
    } else if (key === 'ArrowDown' || key === 'ArrowUp') {
      event.preventDefault()
      if (!open) begin(key === 'ArrowUp')
      else {
        const index = enabled.indexOf(activeIndex)
        setActive(enabled[(index + (key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length] ?? -1)
      }
    } else if (key === 'Home' || key === 'End') {
      event.preventDefault()
      if (!open) begin(key === 'End')
      setActive(enabled[key === 'End' ? enabled.length - 1 : 0] ?? -1)
    } else if (key === 'Enter' || key === ' ') {
      event.preventDefault()
      if (open) choose(activeIndex)
      else begin()
    } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault()
      const now = Date.now()
      const prior = now - search.current.time < 700 ? search.current.text : ''
      const text = prior === key.toLowerCase() ? prior : prior + key.toLowerCase()
      search.current = { text, time: now }
      const start = open ? activeIndex : selected
      const candidates = [...enabled.filter((i) => i > start), ...enabled.filter((i) => i <= start)]
      const match = candidates.find((i) => options[i].label.toLowerCase().startsWith(text))
      if (!open) begin()
      if (match !== undefined) setActive(match)
    }
  }

  return (
    <span className={`custom-select ${className}`} style={{ width }}>
      <button ref={trigger} type="button" className="input select-trigger" role="combobox"
        aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? id : undefined}
        aria-activedescendant={open && activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined}
        disabled={!enabled.length} onKeyDown={onKeyDown}
        onBlur={() => setOpen(false)} onClick={() => open ? setOpen(false) : begin()}>
        {width === undefined ? options.map((option, i) => <span key={i} className="select-sizer" aria-hidden>{option.label}</span>) : null}
        <span className="select-value">{options[selected]?.label ?? String(value)}</span>
        <Icon name="chevronDown" size={16} className={`select-arrow ${open ? 'open' : ''}`} />
      </button>
      {open ? createPortal(
        <div ref={menu} id={id} role="listbox" aria-label={ariaLabel} className="select-menu" style={position}
          onMouseDown={(event) => event.preventDefault()}>
          {options.map((option, i) => (
            <div key={i} id={`${id}-option-${i}`} role="option" aria-selected={i === selected}
              aria-disabled={!!option.disabled} className={`select-option ${i === activeIndex ? 'active' : ''}`}
              onMouseMove={() => !option.disabled && setActive(i)} onClick={() => choose(i)}>
              <span className="select-check">{i === selected ? <Icon name="check" size={14} /> : null}</span>
              <span>{option.label}</span>
            </div>
          ))}
        </div>, document.body,
      ) : null}
    </span>
  )
}
