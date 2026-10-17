import { describe, expect, it } from 'vitest'
import { badgeOverlapDrawings, usableOverlapClock } from './badgeOverlap'
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
    expect(labels[0].text).toContain('110.000–110.250')
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
    expect(label?.text).toContain('2 timeframes')
    expect(label?.text).not.toContain('8 timeframes')
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
  expect(labels[0].text).toContain('Locked Demand · 2 timeframes')
  expect(labels[0].tooltip).toContain('Timeframe count and source zones are frozen at locking')
})

it('does not display a stale locked snapshot while a possible touch is being calculated', () => {
  const locked = {...result, lock_mode: true}
  expect(usableOverlapClock(locked, 1001, false)).toBe(false)
  expect(usableOverlapClock(locked, 999, true)).toBe(false)
  expect(usableOverlapClock(locked, 1001, true)).toBe(true)
})
