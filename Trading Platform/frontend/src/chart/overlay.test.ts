import { describe, expect, it, vi } from 'vitest'
import type { IChartApi, ISeriesApi, SeriesAttachedParameter, SeriesType, Time } from 'lightweight-charts'
import type { LabelObj } from '../api/types'
import { OverlayPrimitive } from './overlay'

function label(id: number, text: string, x: number): LabelObj {
  return { kind: 'label', id, x, y: 100, text, yloc: 'abovebar', style: 'label_down',
    color: '#771111', text_color: '#ff0000', size: 'small', tooltip: text, group: 'patterns', created_at: x }
}

describe('overlay annotation rendering', () => {
  it('paints zone fills and borders after the grid and before series without painting them twice', () => {
    const overlay = new OverlayPrimitive()
    const chart = { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: 0, to: 2 }),
      logicalToCoordinate: (i: number) => 100 + i * 50 }) } as unknown as IChartApi
    const series = { priceToCoordinate: (p: number) => 200 - p } as unknown as ISeriesApi<SeriesType>
    overlay.attached({ chart, series, requestUpdate: () => {} } as SeriesAttachedParameter<Time>)
    overlay.times = [100, 160]
    overlay.layers = [{ uid: 'one', name: 'Zones', shapes: [], drawings: [{
      kind: 'box', id: 1, left: 100, right: 160, top: 100, bottom: 90,
      bg_color: '#08998180', border_color: '#089981', border_width: 1, border_style: 'solid',
      extend: 'none', text: '', text_color: '#ffffff', text_size: 'small',
      text_halign: 'center', text_valign: 'center', tooltip: 'Support zone', group: 'pivot_sr', created_at: 100,
    }] }]
    const fillRect = vi.fn()
    const strokeRect = vi.fn()
    const ctx = new Proxy({ fillRect, strokeRect },
      { get: (target, key) => key in target ? target[key as keyof typeof target] : () => {} })
    const target = { useMediaCoordinateSpace: (draw: (scope: unknown) => void) => draw({ context: ctx, mediaSize: { width: 600, height: 500 } }) }
    // The installed library runs normal drawBackground after its grid and before
    // series draw; a bottom view would put the grid over the box again.
    const views = overlay.paneViews()
    expect(views.some((v) => v.zOrder?.() === 'bottom')).toBe(false)
    const zoneView = views.find((v) => v.renderer()?.drawBackground)!
    expect(zoneView.zOrder?.()).toBe('normal')
    zoneView.renderer()!.drawBackground!(target as never)
    expect(fillRect).toHaveBeenCalledExactlyOnceWith(100, 100, 50, 10)
    expect(strokeRect).toHaveBeenCalledExactlyOnceWith(100.5, 100.5, 50, 10)
    for (const view of views) view.renderer()!.draw(target as never)
    expect(fillRect).toHaveBeenCalledTimes(1)
    expect(strokeRect).toHaveBeenCalledTimes(1)
    expect(overlay.hit(125, 105)?.body).toBe('Support zone')
  })

  it('separates real label and shape paths across layers, measures full text, and moves tooltip regions', () => {
    const overlay = new OverlayPrimitive()
    const chart = { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: 0, to: 3 }),
      logicalToCoordinate: (i: number) => 200 + i * 7, width: () => 600 }) } as unknown as IChartApi
    const series = { priceToCoordinate: () => 300 } as unknown as ISeriesApi<SeriesType>
    overlay.attached({ chart, series, requestUpdate: () => {} } as SeriesAttachedParameter<Time>)
    overlay.times = [100, 160, 220]
    overlay.highs = [100, 100, 100]
    overlay.lows = [90, 90, 90]
    const drawings = [label(1, 'LUS', 100), label(2, 'SS', 100), label(3, 'LUS', 160)]
    const before = JSON.stringify(drawings)
    overlay.layers = [
      { uid: 'one', name: 'Patterns', drawings, shapes: [] },
      { uid: 'two', name: 'Second indicator', drawings: [label(4, 'Multiline\nconfirmation', 160)], shapes: [
        { id: 'sell', title: 'Sell', shape: 'triangledown', location: 'abovebar', color: '#ffff00',
          text: 'Sell\nconfirmation', textColor: '#ffffff', size: 'normal',
          points: [{ time: 100, price: null, text: null, color: null, tooltip: 'Marker' }] },
      ] },
    ]
    const text = vi.fn()
    const ctx = new Proxy({ measureText: (s: string) => ({ width: s.length * 7 }), fillText: text },
      { get: (target, key) => key in target ? target[key as keyof typeof target] : () => {} })
    const target = { useMediaCoordinateSpace: (draw: (scope: unknown) => void) => draw({ context: ctx, mediaSize: { width: 600, height: 500 } }) }
    // Exercise the same foreground renderer Lightweight Charts calls in the browser.
    overlay.paneViews().find((view) => view.zOrder?.() === 'top')!.renderer()!.draw(target as never)
    const regions = (overlay as unknown as { regions: { x0: number; y0: number; x1: number; y1: number }[] }).regions
    expect(regions).toHaveLength(5)
    for (let i = 0; i < regions.length; i++) {
      const a = regions[i]
      for (const b of regions.slice(i + 1)) {
        expect(a.x1 < b.x0 || b.x1 < a.x0 || a.y1 < b.y0 || b.y1 < a.y0).toBe(true)
      }
    }
    expect(overlay.hit((regions[1].x0 + regions[1].x1) / 2, (regions[1].y0 + regions[1].y1) / 2)?.body).toBe('SS')
    expect(text.mock.calls.map((call) => call[0])).toEqual(['LUS', 'SS', 'LUS', 'Multiline', 'confirmation', 'Sell', 'confirmation'])
    expect(JSON.stringify(drawings)).toBe(before)
  })

  it('protects BULB buy/sell text from swing labels, marker connectors and the chart crosshair', () => {
    const overlay = new OverlayPrimitive()
    const chart = { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: 0, to: 2 }),
      logicalToCoordinate: (i: number) => 200 + i * 7, width: () => 600 }) } as unknown as IChartApi
    const series = { priceToCoordinate: (p: number) => p === 100 ? 100 : 300 } as unknown as ISeriesApi<SeriesType>
    overlay.attached({ chart, series, requestUpdate: () => {} } as SeriesAttachedParameter<Time>)
    overlay.times = [100, 160]
    overlay.highs = [100, 100]
    overlay.lows = [90, 90]
    overlay.layers = [{ uid: 'one', name: 'SMC Master', drawings: [
      { ...label(1, 'HH', 100), yloc: 'price', color: '#00000000', group: 'swing_points' },
      { ...label(2, 'sell', 100), yloc: 'price', color: '#F70700FF', text_color: '#FFFFFFFF', size: 'normal', group: 'bulb' },
      { ...label(3, 'HL', 160), y: 90, yloc: 'price', style: 'label_up', color: '#00000000', group: 'swing_points' },
      { ...label(4, 'buy', 160), y: 90, yloc: 'price', style: 'label_up', color: '#22E139FF', text_color: '#FFFFFFFF', size: 'normal', group: 'bulb' },
    ], shapes: [{ id: 'test', title: 'Reaction', shape: 'triangledown', location: 'abovebar',
      color: '#ffff00', text: '', textColor: '#ffffff', size: 'normal',
      points: [{ time: 100, price: null, text: null, color: null, tooltip: null }] }] }]
    const events: string[] = []
    const textCalls: { text: string; font: unknown }[] = []
    const rects: number[][] = []
    const state: Record<string, unknown> = {
      measureText: (s: string) => ({ width: s.length * 7 }),
      rect: (...coords: number[]) => rects.push(coords),
      clip: (rule: string) => events.push(`clip:${rule}`),
      stroke: () => events.push('connector stroke'),
      fillText: (s: string) => { events.push(`text:${s}`); textCalls.push({ text: s, font: state.font }) },
    }
    const ctx = new Proxy(state, { get: (target, key) => key in target ? target[key as string] : () => {} })
    const target = { useMediaCoordinateSpace: (draw: (scope: unknown) => void) => draw({ context: ctx, mediaSize: { width: 600, height: 500 } }) }
    // Labels must be on the top primitive pass (painted after Lightweight Charts' crosshair).
    const top = overlay.paneViews().find((view) => view.zOrder?.() === 'top')!
    top.renderer()!.draw(target as never)
    const regions = (overlay as unknown as { regions: { x0: number; y0: number; x1: number; y1: number }[] }).regions
    expect(regions).toHaveLength(5)
    // Every annotation is cut out of the connector clip, including transparent HH/HL labels.
    for (const r of regions) expect(rects).toContainEqual([r.x0 - 1, r.y0 - 1, r.x1 - r.x0 + 2, r.y1 - r.y0 + 2])
    expect(events.indexOf('clip:evenodd')).toBeLessThan(events.indexOf('connector stroke'))
    expect(events.lastIndexOf('connector stroke')).toBeLessThan(events.indexOf('text:HH'))
    expect(textCalls.map((t) => t.text)).toEqual(['HH', 'sell', 'HL', 'buy'])
    for (const t of textCalls.filter((t) => t.text === 'buy' || t.text === 'sell')) expect(t.font).toMatch(/^11px /)
    expect(overlay.hit((regions[1].x0 + regions[1].x1) / 2, (regions[1].y0 + regions[1].y1) / 2)?.body).toBe('sell')
    expect(overlay.hit((regions[3].x0 + regions[3].x1) / 2, (regions[3].y0 + regions[3].y1) / 2)?.body).toBe('buy')
  })
})
