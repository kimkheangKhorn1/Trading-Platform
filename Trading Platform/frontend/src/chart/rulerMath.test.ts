import { describe, expect, it } from 'vitest'
import { rulerDuration, rulerMetrics, rulerTime } from './rulerMath'

describe('ruler measurement', () => {
  const times = [1000, 1300, 1600, 174400] // three intervals, including a two-day session gap
  it('counts actual bar intervals while elapsed time includes the session gap', () => {
    const result = rulerMetrics({ time: 1000, price: 4000 }, { time: 174400, price: 4020 }, times, 300, 3)
    expect(result).toEqual({ change: 20, percent: 0.5, ticks: 20000, bars: 3, elapsed: 173400 })
    expect(rulerDuration(result.elapsed)).toBe('2d 10m')
  })
  it('keeps price movement signed, including right-to-left measurements', () => {
    const result = rulerMetrics({ time: 1600, price: 4000 }, { time: 1000, price: 3990 }, times, 300, 3)
    expect(result).toEqual({ change: -10, percent: -0.25, ticks: -10000, bars: 2, elapsed: 600 })
  })
  it('snaps candle columns and extrapolates blank future space', () => {
    expect(rulerTime(times, 1.4, 300)).toBe(1300)
    expect(rulerTime(times, 1.6, 300)).toBe(1600)
    expect(rulerTime(times, 5, 300)).toBe(175000)
    expect(rulerTime(times, -2, 300)).toBe(400)
  })
  it('handles a horizontal range and a zero starting price without an invalid percentage', () => {
    expect(rulerMetrics({ time: 1000, price: 0 }, { time: 1600, price: 0 }, times, 300, 3))
      .toEqual({ change: 0, percent: null, ticks: 0, bars: 2, elapsed: 600 })
  })
})
