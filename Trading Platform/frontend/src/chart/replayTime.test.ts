import { describe, expect, it } from 'vitest'
import type { BarRow } from '../api/types'
import { barCloseTime, completedReplayIndex } from './replayTime'

const bar = (time: number, complete = 1): BarRow => [time, 100, 110, 90, 105, 1, complete]
describe('timeframe-independent replay clock', () => {
  it('does not expose the completed OHLC of an hour that has not closed yet', () => {
    const hours = [bar(0), bar(3600), bar(7200)]
    expect(completedReplayIndex(hours, 'H1', 7500)).toBe(1)
    expect(completedReplayIndex(hours, 'H1', 10800)).toBe(2)
  })
  it('the same cutoff restores the exact original 5m bar after changing to 1m or 1H', () => {
    const cutoff = 7500
    const five = Array.from({ length: 30 }, (_, i) => bar(i * 300))
    const one = Array.from({ length: 150 }, (_, i) => bar(i * 60))
    expect(five[completedReplayIndex(five, 'M5', cutoff)][0]).toBe(7200)
    expect(one[completedReplayIndex(one, 'M1', cutoff)][0]).toBe(7440)
    expect(completedReplayIndex([bar(0), bar(3600), bar(7200)], 'H1', cutoff)).toBe(1)
    expect(five[completedReplayIndex(five, 'M5', cutoff)][0]).toBe(7200)
  })
  it('handles session gaps, missing pre-cutoff history, and incomplete candles', () => {
    expect(completedReplayIndex([bar(0), bar(300), bar(172800)], 'M5', 50000)).toBe(1)
    expect(completedReplayIndex([bar(7200)], 'H1', 7500)).toBe(-1)
    expect(completedReplayIndex([bar(0), bar(300, 0)], 'M5', 600)).toBe(0)
  })
  it('uses actual NY daily-session close boundaries through both DST changes', () => {
    const stamp = (s: string) => Date.parse(s) / 1000
    expect(barCloseTime(stamp('2026-03-07T22:00:00Z'), 'D')).toBe(stamp('2026-03-08T21:00:00Z'))
    expect(barCloseTime(stamp('2026-10-31T21:00:00Z'), 'D')).toBe(stamp('2026-11-01T22:00:00Z'))
    expect(barCloseTime(stamp('2026-03-07T00:00:00Z'), 'D', 'utc')).toBe(stamp('2026-03-08T00:00:00Z'))
  })
})
