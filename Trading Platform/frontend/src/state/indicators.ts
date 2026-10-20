import { create } from 'zustand'
import type { IndicatorOutput, InputValue, LineStyle, ScriptMeta, TimeframeCode } from '../api/types'
import { loadRaw, save } from '../lib/storage'
import { ALL_TFS } from './app'
import type { BadgeOverlapSettings } from '../api/badgeOverlap'
import type { ObTradeSettings } from '../api/obSignals'

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
  badgeOverlap?: BadgeOverlapSettings
  /** order block trade signals on the 5m chart (SMC indicator) */
  obTrade?: ObTradeSettings
  visible: boolean
  plotStyles: Record<string, PlotStyle>
  shapeStyles: Record<string, ShapeStyle>
  groupVisible: Record<string, boolean>
  timeframes: TimeframeCode[]
  /** earlier defaults already moved to the current default, as "inputId=value": done once, so a value the user
   *  picks later is never reset */
  upgradedDefaults?: string[]
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
    upgradedDefaults: replacedDefaultKeys(meta), // a new instance starts at the current defaults
  }
}

function replacedDefaultKeys(meta: ScriptMeta): string[] {
  return [
    ...meta.inputs.flatMap((i) => (i.replaced_defaults ?? []).map((v) => `${i.id}=${JSON.stringify(v)}`)),
    ...meta.plots.flatMap((p) => (p.replaced_colors ?? []).map((c) => colorKey('plot', p.id, c))),
    ...meta.shapes.flatMap((p) => (p.replaced_colors ?? []).map((c) => colorKey('shape', p.id, c))),
  ]
}

const colorKey = (kind: 'plot' | 'shape', id: string, color: string) => `${kind}:${id}.color=${color}`

/** A saved plot/shape color still at an earlier default takes the current default, once (like inputs). */
function upgradeColors<T extends { color: string }>(
  kind: 'plot' | 'shape', saved: Record<string, T>, specs: { id: string; color: string; replaced_colors?: string[] | null }[], done: Set<string>,
): Record<string, T> {
  return Object.fromEntries(Object.entries(saved).map(([id, st]) => {
    const spec = specs.find((s) => s.id === id)
    const stale = spec?.replaced_colors?.includes(st.color) && !done.has(colorKey(kind, id, st.color))
    return [id, stale ? { ...st, color: spec!.color } : st]
  }))
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
  /** Drop instances of scripts that no longer exist, fill in inputs/styles added since they were saved and move
   *  settings still at an earlier default to the current default. */
  syncWithMeta: (scripts) => {
    const byId = new Map(scripts.map((s) => [s.id, s]))
    const next = get()
      .instances.filter((i) => byId.has(i.scriptId))
      .map((i) => {
        const meta = byId.get(i.scriptId)!
        const d = defaultsFor(meta)
        // drop inputs the script no longer has; a value still at an earlier default takes the new default, once
        const done = new Set(i.upgradedDefaults ?? [])
        const validInputs = Object.fromEntries(
          Object.entries(i.inputs).filter(([k, v]) => {
            const spec = meta.inputs.find((s) => s.id === k)
            return spec && !(spec.replaced_defaults?.includes(v) && !done.has(`${k}=${JSON.stringify(v)}`))
          }),
        )
        return {
          ...i,
          badgeOverlap: i.badgeOverlap ? {
            enabled: i.badgeOverlap.enabled, timeframes: i.badgeOverlap.timeframes,
            minTimeframes: i.badgeOverlap.minTimeframes,
            strongestOnly: i.badgeOverlap.strongestOnly ?? false,
            candleReactions: i.badgeOverlap.candleReactions ?? true,
            ...(i.badgeOverlap.signals !== undefined ? {signals: i.badgeOverlap.signals} : {}),
          } : undefined,
          obTrade: i.obTrade ? {
            enabled: i.obTrade.enabled, timeframes: i.obTrade.timeframes, confirmations: i.obTrade.confirmations,
            types: i.obTrade.types, zones: i.obTrade.zones ?? true,
          } : undefined,
          inputs: { ...d.inputs, ...validInputs },
          plotStyles: { ...d.plotStyles, ...upgradeColors('plot', i.plotStyles, meta.plots, done) },
          shapeStyles: { ...d.shapeStyles, ...upgradeColors('shape', i.shapeStyles, meta.shapes, done) },
          groupVisible: { ...d.groupVisible, ...i.groupVisible },
          upgradedDefaults: [...new Set([...done, ...replacedDefaultKeys(meta)])],
        }
      })
    set({ instances: next })
    save(KEY, next)
  },
}))
