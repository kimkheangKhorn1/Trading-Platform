import { describe, expect, it } from 'vitest'
import type { ScriptMeta } from '../api/types'
import { useIndicators, type IndicatorInstance } from './indicators'

const meta = {
  id: 's', name: 'S', short_name: 'S', overlay: true, description: '', category: '', plots: [], shapes: [], strategies: [],
  drawing_groups: [],
  inputs: [
    { id: 'mult', type: 'float', title: 'Zone Width', default: 1.0, minval: null, maxval: null, step: null, options: null,
      group: null, inline: null, tooltip: null, replaced_defaults: [0.5] },
    { id: 'len', type: 'int', title: 'Length', default: 25, minval: null, maxval: null, step: null, options: null,
      group: null, inline: null, tooltip: null },
  ],
} as unknown as ScriptMeta

const saved = (inputs: Record<string, number>, upgradedDefaults?: string[]): IndicatorInstance => ({
  uid: 'u', scriptId: 's', inputs, visible: true, plotStyles: {}, shapeStyles: {}, groupVisible: {}, timeframes: [],
  upgradedDefaults,
})

const sync = (inst: IndicatorInstance) => {
  useIndicators.setState({ instances: [inst] })
  useIndicators.getState().syncWithMeta([meta])
  return useIndicators.getState().instances[0]
}

describe('saved indicator settings after a default changed', () => {
  it('moves a value still at the old default to the new default, once', () => {
    const after = sync(saved({ mult: 0.5, len: 25 }))
    expect(after.inputs.mult).toBe(1.0)
    expect(after.upgradedDefaults).toEqual(['mult=0.5'])
  })

  it('keeps a value the user chose', () => {
    expect(sync(saved({ mult: 0.8, len: 30 })).inputs).toEqual({ mult: 0.8, len: 30 })
  })

  it('never resets the old value when the user picks it again later', () => {
    expect(sync(saved({ mult: 0.5, len: 25 }, ['mult=0.5'])).inputs.mult).toBe(0.5)
  })
})
