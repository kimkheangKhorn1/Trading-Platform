import { useState } from 'react'

/** Number input that only commits valid values (keeps the text while typing). */
export function NumberField({
  value, onChange, min, max, step, integer, width = 90, allowNull, placeholder, ariaLabel,
}: {
  value: number | null
  onChange: (v: number | null) => void
  min?: number | null
  max?: number | null
  step?: number | null
  integer?: boolean
  width?: number
  allowNull?: boolean
  placeholder?: string
  ariaLabel?: string
}) {
  const [text, setText] = useState(value === null ? '' : String(value))
  const [err, setErr] = useState<string | null>(null)
  const [seen, setSeen] = useState(value)
  if (seen !== value) {
    // the value changed: show it unless the text being typed already represents it (e.g. "1." while typing 1.5)
    setSeen(value)
    const typed = text.trim() === '' ? null : Number(text)
    if (typed !== value) {
      setText(value === null ? '' : String(value))
      setErr(null)
    }
  }
  const commit = (raw: string) => {
    setText(raw)
    if (raw.trim() === '' && allowNull) {
      setErr(null)
      onChange(null)
      return
    }
    const v = Number(raw)
    if (raw.trim() === '' || Number.isNaN(v)) return setErr('Enter a number')
    if (integer && !Number.isInteger(v)) return setErr('Whole number required')
    if (min !== null && min !== undefined && v < min) return setErr(`Min ${min}`)
    if (max !== null && max !== undefined && v > max) return setErr(`Max ${max}`)
    setErr(null)
    onChange(v)
  }
  return (
    <span className="field-wrap">
      <input className={`input num ${err ? 'invalid' : ''}`} style={{ width }} type="number" value={text}
        step={step ?? (integer ? 1 : 'any')} min={min ?? undefined} max={max ?? undefined} placeholder={placeholder}
        aria-label={ariaLabel} aria-invalid={!!err} onChange={(e) => commit(e.target.value)} />
      {err ? <span className="field-err">{err}</span> : null}
    </span>
  )
}

export function Checkbox({ checked, onChange, label, title }: { checked: boolean; onChange: (v: boolean) => void; label?: string; title?: string }) {
  return (
    <label className="checkbox" title={title}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="box" aria-hidden />
      {label ? <span>{label}</span> : null}
    </label>
  )
}

export { Select } from './Select'

export { ColorField } from './ColorPicker'
