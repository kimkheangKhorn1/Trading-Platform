import { describe, expect, it } from 'vitest'
import { indexAtOrBefore, timeToLogical } from './timeIndex'

describe('timeToLogical', () => {
  const times = [1000, 1300, 1600, 10_000, 10_300] // weekend-like gap after 1600
  it('maps exact bar times to their index', () => {
    expect(timeToLogical(times, 1300, 300)).toBe(1)
    expect(timeToLogical(times, 10_300, 300)).toBe(4)
  })
  it('interpolates between adjacent bars', () => {
    expect(timeToLogical(times, 1150, 300)).toBeCloseTo(0.5)
  })
  it('does not stretch across gaps', () => {
    expect(timeToLogical(times, 5000, 300)).toBe(3) // capped at the next bar index
    expect(timeToLogical(times, 1750, 300)).toBeCloseTo(2.5)
  })
  it('extrapolates outside the data', () => {
    expect(timeToLogical(times, 10_300 + 900, 300)).toBe(7)
    expect(timeToLogical(times, 400, 300)).toBe(-2)
  })
})

describe('indexAtOrBefore', () => {
  const times = [10, 20, 30]
  it('finds the bar containing a time', () => {
    expect(indexAtOrBefore(times, 20)).toBe(1)
    expect(indexAtOrBefore(times, 25)).toBe(1)
    expect(indexAtOrBefore(times, 99)).toBe(2)
    expect(indexAtOrBefore(times, 5)).toBe(-1)
  })
})
