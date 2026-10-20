import { describe, expect, it } from 'vitest'
import { BADGE_OVERLAP_DEFAULTS, badgeOverlapDrawings, usableOverlapClock, withoutTouchedLocks } from './badgeOverlap'
import { overlapSettings } from '../chart/useBadgeOverlaps'
import type { BadgeOverlapResult, OverlapBand } from './badgeOverlap'

const band = (side: 'demand' | 'supply', strength: number, bottom: number): OverlapBand => ({
  side, strength, bottom, top: bottom + .25, timeframes: ['M1', 'M5', 'M15'].slice(0, strength), zone_count: strength,
  badges: ['M1', 'M5', 'M15'].slice(0, strength).map(timeframe => ({timeframe, side, bottom: bottom - 1, top: bottom + 1, price: bottom + 1, origin: 100})),
})
const result: BadgeOverlapResult = {as_of: 1000, overlaps: [band('demand', 2, 100), band('demand', 3, 110),
  band('demand', 3, 120), band('supply', 2, 130)], badges: [], sources: [], total_overlaps: 4, warnings: []}

describe('overlap display precision', () => {
  it('retains exact shared bounds and source provenance, rather than widening to source zones', () => {
    const drawings = badgeOverlapDrawings(result, 900, 60, 3)
    const box = drawings.find(d => d.kind === 'box')
    expect(box).toMatchObject({bottom: 100, top: 100.25, created_at: 1000, overlap: {asOf: 1000, strongest: false}})
    expect(box?.overlap?.band.badges[0].bottom).toBe(99)
    expect(drawings.find(d => d.kind === 'label')?.tooltip).toContain('2 different timeframes')
  })
  it('highlights the maximum on each side and preserves all tied price bands', () => {
    const labels = badgeOverlapDrawings(result, 900, 60, 3, true).filter(d => d.kind === 'label')
    expect(labels).toHaveLength(3)
    expect(labels.map(d => d.overlap?.band.bottom)).toEqual([110, 120, 130])
    expect(labels.every(d => d.text.startsWith('★'))).toBe(true)
    expect(labels[0].text).toBe('★ Demand · 3 TF')
    expect(labels[0].tooltip).toContain('110.000 – 110.250')
  })
  it('blocks future overlap snapshots on rewind and while the chart has no clock', () => {
    expect(usableOverlapClock(result, 999)).toBe(false)
    expect(usableOverlapClock(result, null)).toBe(false)
    expect(usableOverlapClock(result, 1000)).toBe(true)
    expect(usableOverlapClock(result, 1001)).toBe(true)
  })
  it('does not turn repeated badges into extra timeframe strength', () => {
    const duplicate = {...result.overlaps[0], zone_count: 8}
    const label = badgeOverlapDrawings({...result, overlaps: [duplicate]}, 900, 60, 3).find(d => d.kind === 'label')
    expect(label?.text).toBe('★ Demand · 2 TF')
    expect(label?.tooltip).toContain('2 different timeframes')
    expect(label?.tooltip).not.toContain('8')
  })
})

it('keeps locked bounds and captured strength even when another lock has a higher count', () => {
  const locked = {...result.overlaps[0], locked_at: 800, lock_id: 'immutable'}
  const data = {...result, lock_mode: true, overlaps: [locked, result.overlaps[1]]}
  const drawings = badgeOverlapDrawings(data, 900, 60, 3, true)
  const [box] = drawings.filter(d => d.kind === 'box')
  expect(box).toMatchObject({bottom: 100, top: 100.25, created_at: 800})
  const labels = drawings.filter(d => d.kind === 'label')
  expect(labels).toHaveLength(2)
  expect(labels[0].text).toBe('Demand · 2 TF')
  expect(labels[0].tooltip).toContain('Locked Demand · 2 timeframes · 100.000–100.250')
  expect(labels[0].tooltip).toContain('Timeframe count and source zones are frozen at locking')
})

it('keeps the previous locks on screen while the next replay step calculates, minus newly touched ones', () => {
  const locked: BadgeOverlapResult = {...result, lock_mode: true}
  expect(usableOverlapClock(locked, 1060)).toBe(true)
  expect(usableOverlapClock(locked, 999)).toBe(false)
  const bar = (t: number, high: number, low: number): [number, number, number, number, number, number, number] => [t, 0, high, low, 0, 1, 1]
  // A bar that opened before the snapshot is ignored; the server already judged it.
  expect(withoutTouchedLocks(locked, [bar(940, 200, 50)]).overlaps).toHaveLength(4)
  // Low 110.1 reaches the tops of the 110 and 120 demand bands, not 100; high 130 reaches the supply bottom.
  const kept = withoutTouchedLocks(locked, [bar(1000, 130, 110.1)]).overlaps.map(b => b.bottom)
  expect(kept).toEqual([100])
  expect(withoutTouchedLocks(result, [bar(1000, 200, 50)])).toBe(result)
})

const reaction = {
  time: 900, confirmed_at: 960, price: 105, timeframe: 'M1' as const, side: 'demand' as const,
  bottom: 104, top: 108, lock_id: 'lock', locked_at: 800, touched_at: 960,
  code: 'H', name: 'Hammer / Shooting Star', bullish: true, reaction: 'rejection' as const,
  patterns: ['Hammer / Shooting Star', 'Long Lower / Upper Shadow'],
}

it('retains a closed reaction symbol after its overlap box has been consumed', () => {
  const drawings = badgeOverlapDrawings({...result, overlaps: [], reactions: [reaction]}, 1000, 60, 3)
  expect(drawings).toHaveLength(1)
  expect(drawings[0]).toMatchObject({kind: 'label', text: '↶ H', x: 900, yloc: 'belowbar', created_at: 960})
  expect(drawings[0].tooltip).toContain('104.000–108.000')
  expect(drawings[0].tooltip).toContain('Long Lower / Upper Shadow')
})

it('never renders future or pre-lock pattern confirmations', () => {
  const drawings = badgeOverlapDrawings({...result, overlaps: [], reactions: [
    {...reaction, confirmed_at: 1001}, {...reaction, locked_at: 901},
  ]}, 1000, 60, 3)
  expect(drawings).toEqual([])
})

it('merges same-candle reactions and keeps opposite directions on separate sides', () => {
  const drawings = badgeOverlapDrawings({...result, overlaps: [], reactions: [reaction,
    {...reaction, lock_id: 'other'}, {...reaction, lock_id: 'bear', bullish: false, code: 'SS'},
  ]}, 1000, 60, 3)
  expect(drawings.map(d => d.kind === 'label' ? d.yloc : '')).toEqual(['belowbar', 'abovebar'])
  expect(drawings[0].tooltip.match(/Demand overlap:/g)).toHaveLength(2)
})

it('shows distinct bullish and bearish engulfing names within the first-touch window', () => {
  const drawings = badgeOverlapDrawings({...result, as_of: 1100, overlaps: [], reactions: [
    {...reaction, code: 'BE'},
    {...reaction, time: 960, confirmed_at: 1020, code: 'BE', bullish: false, name: 'Bearish Engulfing'},
  ]}, 3000, 60, 3)
  expect(drawings.map(d => d.kind === 'label' ? d.text : '')).toEqual(['↶ Bull Eng', '↶ Bear Eng'])
  expect(drawings[1].tooltip).toContain('Candle 2 of 10 · first-touch candle + next 9 candles')
})

it('describes neutral supply patterns without inventing a bearish or bullish direction', () => {
  const [label] = badgeOverlapDrawings({...result, overlaps: [], reactions: [
    {...reaction, neutral: true, bullish: false, side: 'supply', code: 'D', reaction: 'unconfirmed'},
  ]}, 1000, 60, 3)
  expect(label).toMatchObject({yloc: 'abovebar', text: '? D'})
  expect(label.tooltip).toContain('Neutral candle pattern')
  expect(label.tooltip).not.toMatch(/Bullish candle pattern|Bearish candle pattern/)
})

it.each(['demand', 'supply'] as const)('keeps the %s overlap when only an outer source is touched', side => {
  const sources = [{bottom: 100, top: 110}, {bottom: 102, top: 109}, {bottom: 104, top: 108}]
  const combined: OverlapBand = {...band(side, 3, 104), top: 108, locked_at: 800, lock_id: 'group',
    badges: sources.map((z, i) => ({...z, timeframe: ['M1', 'M5', 'M15'][i], side,
      price: side === 'demand' ? z.top : z.bottom, origin: 100}))}
  const data = {...result, lock_mode: true, overlaps: [combined]}
  // Neither candle reaches the shared 104–108 band, but each touches the outermost source.
  const candle: [number, number, number, number, number, number, number] = side === 'demand'
    ? [1000, 112, 113, 109.5, 112, 1, 1] : [1000, 98, 100.5, 97, 98, 1, 1]
  expect(withoutTouchedLocks(data, [candle]).overlaps).toEqual([combined])
  expect(combined.strength).toBe(3)
  expect(combined.badges).toHaveLength(3)
})

it('removes only the reached overlap while three higher bands sharing H4 stay identical', () => {
  const bands = [100, 110, 120, 130].map(bottom => ({...band('supply', 3, bottom), top: bottom+2,
    locked_at: 800, badges: [
      {timeframe:'H4', side:'supply' as const, bottom:100, top:140, price:100, origin:100},
      {timeframe:'M5', side:'supply' as const, bottom, top:bottom+2, price:bottom, origin:bottom},
      {timeframe:'M15', side:'supply' as const, bottom, top:bottom+2, price:bottom, origin:bottom},
    ]}))
  const after = withoutTouchedLocks({...result, lock_mode:true, overlaps:bands}, [[1000,98,101,97,100,1,1]])
  expect(after.overlaps).toEqual(bands.slice(1))
})

it('keeps rejection and continuation at different zones distinct on the same candle', () => {
  const labels = badgeOverlapDrawings({...result, overlaps: [], reactions: [
    {...reaction, code:'BE', name:'Bullish Engulfing'},
    {...reaction, code:'BE', name:'Bullish Engulfing', side:'supply', lock_id:'supply-break', reaction:'continuation'},
  ]}, 1000, 60, 3).filter(d => d.kind === 'label')
  expect(labels.map(d => d.text)).toEqual(['↶ Bull Eng', '➜ Bull Eng'])
  expect(labels[0].tooltip).not.toContain('Supply overlap:')
  expect(labels[1].tooltip).not.toContain('Demand overlap:')
})

it('does not merge a neutral pattern with a directional pattern at another zone', () => {
  const labels = badgeOverlapDrawings({...result, overlaps: [], reactions: [reaction,
    {...reaction, neutral:true, code:'D', name:'Doji', lock_id:'neutral', reaction:'unconfirmed'},
  ]}, 1000, 60, 3).filter(d => d.kind === 'label')
  expect(labels.map(d => d.text)).toEqual(['↶ H', '? D'])
  expect(labels[1].tooltip).toContain('Neutral candle pattern')
})

it('keeps 1m reactions through candle 10 and rejects stale candle-11 records', () => {
  const labels=badgeOverlapDrawings({...result, as_of:2000, overlaps:[], reactions:[
    {...reaction,time:1020,confirmed_at:1080},
    {...reaction,time:1440,confirmed_at:1500,code:'BE',name:'Bullish Engulfing'},
    {...reaction,time:1500,confirmed_at:1560,code:'D',name:'Doji'},
    {...reaction,confirmed_at:961},
  ]}, 2000,60,3).filter(d=>d.kind==='label')
  expect(labels).toHaveLength(2)
  expect(labels[0].tooltip).toContain('Candle 3 of 10')
  expect(labels[1].tooltip).toContain('Candle 10 of 10')
})

it('counts a mid-5m touch as candle one and rejects candle four', () => {
  const labels=badgeOverlapDrawings({...result,as_of:3000,overlaps:[],reactions:[
    {...reaction,timeframe:'M5',time:900,confirmed_at:1200,touched_at:1200},
    {...reaction,timeframe:'M5',time:1500,confirmed_at:1800,touched_at:1200},
    {...reaction,timeframe:'M5',time:1800,confirmed_at:2100,touched_at:1200},
  ]},3000,300,3).filter(d=>d.kind==='label')
  expect(labels).toHaveLength(2)
  expect(labels[0].tooltip).toContain('Candle 1 of 3')
  expect(labels[1].tooltip).toContain('Candle 3 of 3')
})


it('never displays LLS as overlap confirmation, including legacy responses', () => {
  const labels=badgeOverlapDrawings({...result,overlaps:[],reactions:[
    {...reaction,code:'LLS',name:'Long Lower Shadow'},
    {...reaction,code:'H',name:'Hammer'},
    {...reaction,code:'LUS',name:'Long Upper Shadow',bullish:false},
  ]},1000,60,3).filter(d=>d.kind==='label')
  expect(labels.map(d=>d.text)).toEqual(['↶ H','↶ LUS'])
})

it('overlap settings default to the D/S defaults', () => {
  const inst = {uid: 'u', scriptId: 'smc_ict_scalping_master', inputs: {}, visible: true, plotStyles: {}, shapeStyles: {},
    groupVisible: {}, timeframes: []} as unknown as Parameters<typeof overlapSettings>[0]
  expect(overlapSettings(inst)).toBe(BADGE_OVERLAP_DEFAULTS)
  expect(overlapSettings({...inst, badgeOverlap: {...BADGE_OVERLAP_DEFAULTS, enabled: true}}).enabled).toBe(true)
})
