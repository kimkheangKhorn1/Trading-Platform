import { create } from 'zustand'
import type { IndicatorOutput, InputValue, LineStyle, ScriptMeta, TimeframeCode } from '../api/types'
import { loadRaw, save } from '../lib/storage'
import { ALL_TFS } from './app'

export interface PlotStyle {
  visible: boolean
  color: string
  linewidth: number
  linestyle: LineStyle
}

export interface ShapeStyle {
  visible: boolean
  color: string
}

export interface IndicatorInstance {
  uid: string
  scriptId: string
  inputs: Record<string, InputValue>
  visible: boolean
  plotStyles: Record<string, PlotStyle>
  shapeStyles: Record<string, ShapeStyle>
  groupVisible: Record<string, boolean>
  timeframes: TimeframeCode[]
}

export interface OutputState {
  data: IndicatorOutput | null
  loading: boolean
  error: string | null
}

interface IndicatorsState {
  instances: IndicatorInstance[]
  outputs: Record<string, OutputState>
  add: (meta: ScriptMeta) => IndicatorInstance
  remove: (uid: string) => void
  update: (uid: string, patch: Partial<IndicatorInstance>) => void
  replace: (inst: IndicatorInstance) => void
  setOutput: (uid: string, out: Partial<OutputState>) => void
  syncWithMeta: (scripts: ScriptMeta[]) => void
}

const KEY = 'tp.indicators.v1'

export function defaultsFor(meta: ScriptMeta): Omit<IndicatorInstance, 'uid'> {
  return {
    scriptId: meta.id,
    inputs: Object.fromEntries(meta.inputs.map((i) => [i.id, i.default])),
    visible: true,
    plotStyles: Object.fromEntries(
      meta.plots.map((p) => [p.id, { visible: p.visible, color: p.color, linewidth: p.linewidth, linestyle: p.linestyle }]),
    ),
    shapeStyles: Object.fromEntries(meta.shapes.map((s) => [s.id, { visible: s.visible, color: s.color }])),
    groupVisible: Object.fromEntries(meta.drawing_groups.map((g) => [g.id, g.visible])),
    timeframes: [...ALL_TFS],
  }
}

export const useIndicators = create<IndicatorsState>((set, get) => ({
  instances: loadRaw<IndicatorInstance[]>(KEY, []),
  outputs: {},

  add: (meta) => {
    const inst: IndicatorInstance = { uid: Math.random().toString(36).slice(2, 10), ...defaultsFor(meta) }
    set({ instances: [...get().instances, inst] })
    save(KEY, get().instances)
    return inst
  },
  remove: (uid) => {
    const outputs = { ...get().outputs }
    delete outputs[uid]
    set({ instances: get().instances.filter((i) => i.uid !== uid), outputs })
    save(KEY, get().instances)
  },
  update: (uid, patch) => {
    set({ instances: get().instances.map((i) => (i.uid === uid ? { ...i, ...patch } : i)) })
    save(KEY, get().instances)
  },
  replace: (inst) => {
    set({ instances: get().instances.map((i) => (i.uid === inst.uid ? inst : i)) })
    save(KEY, get().instances)
  },
  setOutput: (uid, out) => {
    const prev = get().outputs[uid] ?? { data: null, loading: false, error: null }
    set({ outputs: { ...get().outputs, [uid]: { ...prev, ...out } } })
  },
  /** Drop instances of scripts that no longer exist and fill in inputs/styles added since they were saved. */
  syncWithMeta: (scripts) => {
    const byId = new Map(scripts.map((s) => [s.id, s]))
    const next = get()
      .instances.filter((i) => byId.has(i.scriptId))
      .map((i) => {
        const meta = byId.get(i.scriptId)!
        const d = defaultsFor(meta)
        const validInputs = Object.fromEntries(
          Object.entries(i.inputs).filter(([k]) => meta.inputs.some((s) => s.id === k)),
        )
        return {
          ...i,
          inputs: { ...d.inputs, ...validInputs },
          plotStyles: { ...d.plotStyles, ...i.plotStyles },
          shapeStyles: { ...d.shapeStyles, ...i.shapeStyles },
          groupVisible: { ...d.groupVisible, ...i.groupVisible },
        }
      })
    set({ instances: next })
    save(KEY, next)
  },
}))
