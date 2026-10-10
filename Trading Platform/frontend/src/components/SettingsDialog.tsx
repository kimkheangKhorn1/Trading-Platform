import { useRef, useState } from 'react'
import type { LineStyle } from '../api/types'
import { ALL_TFS, TF_LABELS, useApp } from '../state/app'
import { defaultsFor, useIndicators, type IndicatorInstance } from '../state/indicators'
import { Checkbox, ColorField, Select } from './common/Fields'
import { Modal, Tabs } from './common/Modal'
import { InputsForm } from './InputsForm'

type Tab = 'inputs' | 'style' | 'visibility'
const SHAPE_NAMES: Record<string, string> = {
  arrowup: 'Arrow up', arrowdown: 'Arrow down', triangleup: 'Triangle up', triangledown: 'Triangle down',
  circle: 'Circle', cross: 'Cross', xcross: 'X', diamond: 'Diamond', square: 'Square', flag: 'Flag',
  labelup: 'Label up', labeldown: 'Label down',
}
const LOCATION_NAMES: Record<string, string> = {
  abovebar: 'above bar', belowbar: 'below bar', top: 'top', bottom: 'bottom', absolute: 'at price',
}

/**
 * Indicator settings (Inputs / Style / Visibility). Like TradingView, edits apply to the chart
 * immediately; Cancel restores the settings the dialog was opened with.
 */
export function SettingsDialog({ uid }: { uid: string }) {
  const scripts = useApp((s) => s.scripts)
  const close = () => useApp.getState().openDialog(null)
  const inst = useIndicators((s) => s.instances.find((i) => i.uid === uid))
  const replace = useIndicators((s) => s.replace)
  const original = useRef<IndicatorInstance | undefined>(inst)
  const [tab, setTab] = useState<Tab>('inputs')
  const meta = scripts.find((s) => s.id === inst?.scriptId)
  if (!inst || !meta) return null
  const patch = (p: Partial<IndicatorInstance>) => replace({ ...inst, ...p })
  const cancel = () => {
    if (original.current) replace(original.current)
    close()
  }
  const reset = () => replace({ ...inst, ...defaultsFor(meta), uid: inst.uid, visible: inst.visible })

  return (
    <Modal title={meta.name} onClose={cancel} width={680} className="settings-dialog"
      footer={
        <>
          <button className="btn ghost" onClick={reset} title="Restore the script's default inputs and styles">Defaults</button>
          <div className="grow" />
          <button className="btn ghost" onClick={cancel}>Cancel</button>
          <button className="btn primary" onClick={close}>Ok</button>
        </>
      }>
      <Tabs<Tab> tabs={[{ id: 'inputs', label: 'Inputs' }, { id: 'style', label: 'Style' }, { id: 'visibility', label: 'Visibility' }]}
        value={tab} onChange={setTab} />
      <div className="settings-body">
        {tab === 'inputs' ? (
          meta.inputs.length ? (
            <InputsForm specs={meta.inputs} values={inst.inputs} onChange={(id, v) => patch({ inputs: { ...inst.inputs, [id]: v } })} />
          ) : <p className="muted">This script has no inputs.</p>
        ) : null}
        {tab === 'style' ? (
          <div className="style-tab">
            {meta.plots.map((p) => {
              const st = inst.plotStyles[p.id]
              const set = (q: Partial<typeof st>) => patch({ plotStyles: { ...inst.plotStyles, [p.id]: { ...st, ...q } } })
              return (
                <div key={p.id} className="style-row">
                  <Checkbox checked={st.visible} onChange={(v) => set({ visible: v })} label={p.title} />
                  <div className="grow" />
                  <ColorField value={st.color} onChange={(c) => set({ color: c })} />
                  <Select value={st.linewidth} ariaLabel="Line width" width={78}
                    options={[1, 2, 3, 4].map((w) => ({ value: w, label: `${w}px` }))} onChange={(w) => set({ linewidth: w })} />
                  <Select<LineStyle> value={st.linestyle} ariaLabel="Line style" width={92}
                    options={[{ value: 'solid', label: 'Solid' }, { value: 'dashed', label: 'Dashed' }, { value: 'dotted', label: 'Dotted' }]}
                    onChange={(s) => set({ linestyle: s })} />
                </div>
              )
            })}
            {meta.shapes.map((s) => {
              const st = inst.shapeStyles[s.id]
              return (
                <div key={s.id} className="style-row">
                  <Checkbox checked={st.visible} label={s.title}
                    onChange={(v) => patch({ shapeStyles: { ...inst.shapeStyles, [s.id]: { ...st, visible: v } } })} />
                  <div className="grow" />
                  <span className="muted small">{SHAPE_NAMES[s.shape] ?? s.shape} · {LOCATION_NAMES[s.location] ?? s.location}</span>
                  <ColorField value={st.color} onChange={(c) => patch({ shapeStyles: { ...inst.shapeStyles, [s.id]: { ...st, color: c } } })} />
                </div>
              )
            })}
            {meta.drawing_groups.length ? <h4 className="group-title">Drawings</h4> : null}
            {meta.drawing_groups.map((g) => (
              <div key={g.id} className="style-row">
                <Checkbox checked={inst.groupVisible[g.id] !== false} label={g.title}
                  onChange={(v) => patch({ groupVisible: { ...inst.groupVisible, [g.id]: v } })} />
              </div>
            ))}
            <p className="muted small note">Colors of drawings that the script exposes as inputs are edited on the Inputs tab, as in TradingView.</p>
          </div>
        ) : null}
        {tab === 'visibility' ? (
          <div className="vis-tab">
            <p className="muted small">Show this indicator on the following timeframes:</p>
            {ALL_TFS.map((tf) => (
              <div key={tf} className="style-row">
                <Checkbox checked={inst.timeframes.includes(tf)} label={TF_LABELS[tf]}
                  onChange={(v) => patch({ timeframes: v ? [...inst.timeframes, tf] : inst.timeframes.filter((x) => x !== tf) })} />
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
