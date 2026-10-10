/**
 * Maps arbitrary UTC timestamps to the chart's logical (bar index) axis.
 *
 * Lightweight Charts can only convert times that exist in the series. Indicator drawings, however,
 * may reference times between bars (other timeframes) or in the future (lines extended to the
 * right), so we interpolate between known bars and extrapolate by the timeframe duration outside.
 */
export function timeToLogical(times: ArrayLike<number>, t: number, tfSeconds: number): number {
  const n = times.length
  if (n === 0) return 0
  if (t <= times[0]) return (t - times[0]) / tfSeconds
  if (t >= times[n - 1]) return n - 1 + (t - times[n - 1]) / tfSeconds
  let lo = 0
  let hi = n - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (times[mid] <= t) lo = mid
    else hi = mid
  }
  if (times[lo] === t) return lo
  // inside a gap (e.g. weekend): stay on the earlier bar until one timeframe has elapsed
  const frac = Math.min(1, (t - times[lo]) / Math.min(tfSeconds, times[hi] - times[lo]))
  return lo + frac
}

/** Index of the bar whose open time equals t, or the last bar opening before t (-1 if none). */
export function indexAtOrBefore(times: ArrayLike<number>, t: number): number {
  let lo = 0
  let hi = times.length - 1
  if (hi < 0 || t < times[0]) return -1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (times[mid] <= t) lo = mid
    else hi = mid - 1
  }
  return lo
}
