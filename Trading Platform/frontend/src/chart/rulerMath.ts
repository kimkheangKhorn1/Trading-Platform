import { timeToLogical } from './timeIndex'

export interface RulerPoint { time: number; price: number }

/** Snap to a candle column; extrapolate in empty space using the active timeframe. */
export function rulerTime(times: number[], logical: number, seconds: number): number {
  const index = Math.round(logical)
  if (index < 0) return times[0] + index * seconds
  if (index >= times.length) return times[times.length - 1] + (index - times.length + 1) * seconds
  return times[index]
}

export function rulerMetrics(start: RulerPoint, end: RulerPoint, times: number[], seconds: number, precision: number) {
  const change = end.price - start.price
  return {
    change,
    percent: start.price === 0 ? null : change / start.price * 100,
    ticks: Math.round(change * 10 ** precision),
    bars: Math.abs(Math.round(timeToLogical(times, end.time, seconds) - timeToLogical(times, start.time, seconds))),
    elapsed: Math.abs(end.time - start.time),
  }
}

export function rulerDuration(seconds: number): string {
  let rest = Math.round(seconds)
  const parts: string[] = []
  for (const [unit, size] of [['d', 86400], ['h', 3600], ['m', 60], ['s', 1]] as const) {
    const value = Math.floor(rest / size)
    if (value) parts.push(`${value}${unit}`)
    rest %= size
  }
  return parts.join(' ') || '0s'
}
