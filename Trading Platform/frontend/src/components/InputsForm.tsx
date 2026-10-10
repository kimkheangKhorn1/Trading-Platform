import { useMemo, useState } from 'react'
import type { InputSpec, InputValue } from '../api/types'
import { TF_LABELS } from '../state/app'
import { Checkbox, ColorField, NumberField, Select } from './common/Fields'
import { HelpTip } from './common/HelpTip'
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

/** Pine titles are sometimes lower-case ("swing High/Low"); show them capitalized. */
const nice = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

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
      return <Checkbox checked={value as boolean} onChange={onChange} label={spec.title ? nice(spec.title) : undefined} />
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

type Group = { name: string; rows: InputSpec[][] }

/** Help text for a row: the hand-written help of its first input, else any Pine tooltip in the row. */
function rowHelp(row: InputSpec[]): { about: string; example: string | null } | null {
  const lead = row.find((s) => s.help)
  if (lead?.help) return { about: lead.help, example: lead.example ?? null }
  const tip = row.find((s) => s.tooltip)?.tooltip
  return tip ? { about: tip.trim(), example: null } : null
}

/** TradingView-like Inputs tab: searchable, collapsible groups, aligned rows and a help card per row. */
export function InputsForm({ specs, values, onChange }: {
  specs: InputSpec[]
  values: Record<string, InputValue>
  onChange: (id: string, v: InputValue) => void
}) {
  const [query, setQuery] = useState('')
  const [closed, setClosed] = useState<Set<string>>(new Set())
  const groups = useMemo(() => {
    const out: Group[] = []
    for (const spec of specs) {
      const name = spec.group ?? ''
      let g = out.find((x) => x.name === name)
      if (!g) {
        g = { name, rows: [] }
        out.push(g)
      }
      const last = g.rows[g.rows.length - 1]
      if (spec.inline && last && last[0].inline === spec.inline) last.push(spec)
      else g.rows.push([spec])
    }
    return out
  }, [specs])

  const q = query.trim().toLowerCase()
  const shown = q
    ? groups.map((g) => ({
        ...g,
        rows: g.name.toLowerCase().includes(q) ? g.rows : g.rows.filter((row) =>
          row.some((s) => [s.title, s.help, s.example, s.tooltip].some((t) => t?.toLowerCase().includes(q)))),
      })).filter((g) => g.rows.length)
    : groups
  const toggle = (name: string) => setClosed((c) => {
    const n = new Set(c)
    if (n.has(name)) n.delete(name)
    else n.add(name)
    return n
  })
  const allClosed = closed.size === groups.length

  return (
    <div className="inputs-form">
      <div className="inputs-toolbar">
        <label className="settings-search">
          <Icon name="search" size={15} />
          <input className="input" placeholder={`Search ${specs.length} settings…`} value={query} aria-label="Search settings"
            onChange={(e) => setQuery(e.target.value)} />
          {query ? <button className="icon-btn sm" aria-label="Clear search" onClick={() => setQuery('')}><Icon name="close" size={13} /></button> : null}
        </label>
        <button className="btn ghost sm" onClick={() => setClosed(allClosed ? new Set() : new Set(groups.map((g) => g.name)))} disabled={!!q}>
          {allClosed ? 'Expand all' : 'Collapse all'}
        </button>
      </div>
      {shown.length === 0 ? <p className="muted inputs-empty">No settings match “{query}”.</p> : null}
      {shown.map((g) => {
        const isOpen = !!q || !closed.has(g.name)
        return (
          <section key={g.name} className={`input-group ${isOpen ? 'open' : ''}`}>
            {g.name ? (
              <button className="group-head" onClick={() => toggle(g.name)} aria-expanded={isOpen} disabled={!!q}>
                <Icon name={isOpen ? 'chevronDown' : 'chevronRight'} size={13} />
                <span className="group-title">{g.name}</span>
                <span className="group-count">{g.rows.length}</span>
              </button>
            ) : null}
            {isOpen ? g.rows.map((row) => {
              const lead = row[0]
              const help = rowHelp(row)
              const rest = lead.type === 'bool' ? row.slice(1) : row
              return (
                <div key={lead.id} className="input-row">
                  <div className="input-label-col">
                    {lead.type === 'bool'
                      ? <Widget spec={lead} value={values[lead.id] ?? lead.default} onChange={(v) => onChange(lead.id, v)} />
                      : <span className="input-label">{nice(lead.title || g.name)}</span>}
                  </div>
                  <div className="input-controls">
                    {rest.map((spec, k) => (
                      <span key={spec.id} className="input-cell">
                        {k > 0 && spec.title && spec.type !== 'bool' ? <span className="cell-label">{nice(spec.title)}</span> : null}
                        <Widget spec={spec} value={values[spec.id] ?? spec.default} onChange={(v) => onChange(spec.id, v)} />
                      </span>
                    ))}
                  </div>
                  <div className="input-help-col">
                    {help ? <HelpTip title={nice(lead.title || g.name)} about={help.about} example={help.example} /> : null}
                  </div>
                </div>
              )
            }) : null}
          </section>
        )
      })}
    </div>
  )
}
