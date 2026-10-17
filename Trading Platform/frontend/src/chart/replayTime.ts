import type { BarRow, TimeframeCode } from '../api/types'
import { TF_SECONDS } from '../state/app'
import { indexAtOrBefore } from './timeIndex'

/** Candle close boundary, including 23/25-hour NY-session daily candles across DST. */
export function barCloseTime(open: number, tf: TimeframeCode, alignment = 'ny17'): number {
  const end = open + TF_SECONDS[tf]
  if (tf !== 'D' || alignment !== 'ny17') return end
  const hour = Number(new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23',
  }).format(new Date(end * 1000)))
  return end + (17 - hour) * 3600
}

/** Never expose the final OHLC of a higher-timeframe candle that closes after the replay clock. */
export function completedReplayIndex(bars: BarRow[], tf: TimeframeCode, cutoff: number, alignment = 'ny17'): number {
  let i = indexAtOrBefore(bars.map((b) => b[0]), cutoff)
  while (i >= 0 && (bars[i][6] === 0 || barCloseTime(bars[i][0], tf, alignment) > cutoff)) i--
  return i
}
