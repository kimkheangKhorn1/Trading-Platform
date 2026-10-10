import type { InputSpec, InputValue } from '../api/types'
import { TF_LABELS } from '../state/app'
import { Checkbox, ColorField, NumberField, Select } from './common/Fields'
import { Icon } from './common/Icon'

const TF_INPUT_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Chart' },
  { value: '1', label: TF_LABELS.M1 },
  { value: '5', label: TF_LABELS.M5 },
  { value: '15', label: TF_LABELS.M15 },
  { value: '30', label: TF_LABELS.M30 },
  { value: '60', label: TF_LABELS.H1 },
  { value: '240', label: TF_LABELS.H4 },
  { value: 'D', label: TF_LABELS.D },
]
const SOURCES = ['open', 'high', 'low', 'close', 'hl2', 'hlc3', 'ohlc4', 'hlcc4']

function Widget({ spec, value, onChange }: { spec: InputSpec; value: InputValue; onChange: (v: InputValue) => void }) {
  switch (spec.type) {
    case 'int':
    case 'float':
      if (spec.options) {
        return <Select ariaLabel={spec.title} value={value as number} options={spec.options.map((o) => ({ value: o as number, label: String(o) }))} onChange={onChange} />
      }
      return (
        <NumberField value={value as number} integer={spec.type === 'int'} min={spec.minval} max={spec.maxval}
          step={spec.step} ariaLabel={spec.title} onChange={(v) => v !== null && onChange(v)} />
      )
    case 'bool':
      return <Checkbox checked={value as boolean} onChange={onChange} label={spec.title} title={spec.tooltip ?? undefined} />
    case 'color':
      return <ColorField value={value as string} onChange={onChange} />
    case 'source':
      return <Select ariaLabel={spec.title} value={value as string} options={SOURCES.map((s) => ({ value: s, label: s }))} onChange={onChange} />
    case 'timeframe':
      return <Select ariaLabel={spec.title} value={value as string} options={TF_INPUT_OPTIONS} onChange={onChange} />
    default:
      if (spec.options) {
        return <Select ariaLabel={spec.title} value={value as string} options={spec.options.map((o) => ({ value: String(o), label: String(o) }))} onChange={onChange} />
      }
      return <input className="input" value={value as string} onChange={(e) => onChange(e.target.value)} aria-label={spec.title} />
  }
}

/** TradingView-like Inputs tab: groups, inline rows, tooltips. */
export function InputsForm({ specs, values, onChange }: {
  specs: InputSpec[]
  values: Record<string, InputValue>
  onChange: (id: string, v: InputValue) => void
}) {
  const groups: { name: string | null; rows: InputSpec[][] }[] = []
  for (const spec of specs) {
    let g = groups.find((x) => x.name === spec.group)
    if (!g) {
      g = { name: spec.group, rows: [] }
      groups.push(g)
    }
    const last = g.rows[g.rows.length - 1]
    if (spec.inline && last && last[0].inline === spec.inline) last.push(spec)
    else g.rows.push([spec])
  }
  return (
    <div className="inputs-form">
      {groups.map((g, gi) => (
        <section key={gi} className="input-group">
          {g.name ? <h4 className="group-title">{g.name}</h4> : null}
          {g.rows.map((row, ri) => (
            <div key={ri} className="input-row">
              {row.map((spec, k) => (
                <span key={spec.id} className={`input-cell ${k === 0 ? 'first' : ''}`}>
                  {spec.type !== 'bool' ? <label className="input-label">{spec.title}</label> : null}
                  <Widget spec={spec} value={values[spec.id] ?? spec.default} onChange={(v) => onChange(spec.id, v)} />
                  {spec.tooltip && spec.type !== 'bool' ? (
                    <span className="tip-icon" title={spec.tooltip}><Icon name="info" size={14} /></span>
                  ) : null}
                  {spec.tooltip && spec.type === 'bool' ? (
                    <span className="tip-icon" title={spec.tooltip}><Icon name="info" size={14} /></span>
                  ) : null}
                </span>
              ))}
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}
