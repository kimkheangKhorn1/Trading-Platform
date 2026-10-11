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

describe('saved plot and shape colors after a default color changed', () => {
  const colored = {
    ...meta,
    plots: [{ id: 'sma', title: 'SMA', color: '#5B8CFFFF', linewidth: 1, style: 'line', linestyle: 'solid', pane: 'overlay', visible: true, replaced_colors: ['#2962FFFF'] }],
    shapes: [{ id: 'up', title: 'Up', shape: 'arrowup', location: 'belowbar', color: '#FF8A3DE6', text: '', textcolor: '#FFFFFFFF', size: 'small', visible: true, replaced_colors: ['#FF6D00BF'] }],
  } as unknown as ScriptMeta
  const styled = (sma: string, up: string, upgradedDefaults?: string[]): IndicatorInstance => ({
    ...saved({ mult: 1.0, len: 25 }, upgradedDefaults),
    plotStyles: { sma: { visible: true, color: sma, linewidth: 2, linestyle: 'solid' } },
    shapeStyles: { up: { visible: true, color: up } },
  })
  const syncColored = (inst: IndicatorInstance) => {
    useIndicators.setState({ instances: [inst] })
    useIndicators.getState().syncWithMeta([colored])
    return useIndicators.getState().instances[0]
  }

  it('moves colors still at the old default to the new ones, keeping the rest of the style', () => {
    const after = syncColored(styled('#2962FFFF', '#FF6D00BF'))
    expect(after.plotStyles.sma).toEqual({ visible: true, color: '#5B8CFFFF', linewidth: 2, linestyle: 'solid' })
    expect(after.shapeStyles.up.color).toBe('#FF8A3DE6')
  })

  it('keeps a color the user chose, and never resets one picked again after the upgrade', () => {
    expect(syncColored(styled('#ABCDEFFF', '#FF6D00BF', ['shape:up.color=#FF6D00BF'])).plotStyles.sma.color).toBe('#ABCDEFFF')
    expect(syncColored(styled('#ABCDEFFF', '#FF6D00BF', ['shape:up.color=#FF6D00BF'])).shapeStyles.up.color).toBe('#FF6D00BF')
  })
})
