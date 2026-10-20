import type { DrawingObj, InputValue, TimeframeCode } from './types'
import type { OverlapTrade } from './badgeOverlap'
import { BADGE_TF_LABELS } from './badgeOverlap'
import { fmtDateTime } from '../lib/format'
import type { TimezoneId } from '../lib/format'

/** Order block trade (5m chart): N confirmation candles at an order block of each selected timeframe. */
export const OB_TFS: TimeframeCode[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4']
export type ObType = 'internal' | 'swing'
export interface ObTradeSettings {
  enabled: boolean
  /** source timeframes whose order blocks are traded, each on its own (not overlaps) */
  timeframes: TimeframeCode[]
  /** 5m candles in a row closing back beyond the block */
  confirmations: number
  types: ObType[]
  /** draw the waiting blocks nearest to price */
  zones?: boolean
}
export const OB_TRADE_DEFAULTS: ObTradeSettings = {
  enabled: false, timeframes: ['M15', 'M30', 'H1'], confirmations: 1, types: ['internal', 'swing'], zones: true,
}
export const OB_CONFIRMATIONS = [1, 2, 3, 4, 5] as const
export const OB_STYLE = {title: 'OB trade', bull: '#22D3A5', bear: '#FF6B81', zoneId: -300000, group: 'ob_signals'} as const

/** An order block trade: the overlap trade plus the block it came from. */
export interface ObTrade extends OverlapTrade {
  ob_timeframe: TimeframeCode; ob_kind: ObType; ob_origin: number; confirmations: number
}
export interface ObZone {
  id: number; timeframe: TimeframeCode; kind: ObType; side: 1 | -1; top: number; bottom: number; origin: number
  known_at: number; touched_at: number | null; count: number
}
export interface ObSignalRequest {
  feed_id: string; inputs: Record<string, InputValue>; as_of: number; timeframes: TimeframeCode[]
  confirmations: number; ob_types: ObType[]; chart_timeframe: TimeframeCode
  signal_buffer: number; signal_max_risk: number; replay_start?: number | null
}
export interface ObSignalResult {
  as_of: number; signals: ObTrade[]; zones: ObZone[]; sources: {timeframe: string; blocks: number}[]; warnings: string[]
}
export async function fetchObSignals(body: ObSignalRequest): Promise<ObSignalResult> {
  const res = await fetch('/api/ob-signals', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Order block signal request failed (${res.status})`)
  return data
}

/** The waiting order blocks of the selected timeframes, from their candle, extended right. */
export function obZoneDrawings(zones: ObZone[], lastTime: number, step: number, precision: number, tz: TimezoneId): DrawingObj[] {
  const f = (p: number) => p.toFixed(precision)
  return zones.flatMap((z, i): DrawingObj[] => {
    const bull = z.side === 1
    const col = bull ? OB_STYLE.bull : OB_STYLE.bear
    const name = `${bull ? 'Bullish' : 'Bearish'} ${z.kind} OB · ${BADGE_TF_LABELS[z.timeframe] ?? z.timeframe}`
    const tooltip = `${name}\nRange: ${f(z.bottom)} – ${f(z.top)}\nOB candle: ${fmtDateTime(z.origin, tz)}\nKnown at: ${fmtDateTime(z.known_at, tz)}${z.touched_at ? `\nTouched: ${fmtDateTime(z.touched_at, tz)} · waiting for confirmation` : '\nNot touched yet'}\nTraded on 5m: a ${bull ? 'BUY' : 'SELL'} after the confirmation candles close ${bull ? 'above' : 'below'} it.`
    const common = {group: OB_STYLE.group, created_at: z.known_at, tooltip}
    return [
      {...common, kind: 'box', id: OB_STYLE.zoneId - i * 2, left: z.origin, right: lastTime + 6 * step, top: z.top, bottom: z.bottom,
        border_color: col + '99', border_width: 1, border_style: 'dashed', bg_color: col + '14', extend: 'none',
        text: '', text_color: '#FFFFFFFF', text_size: 'small', text_halign: 'right', text_valign: 'center'},
      {...common, kind: 'label', id: OB_STYLE.zoneId - 1 - i * 2, x: lastTime + 6 * step, y: bull ? z.top : z.bottom,
        text: `OB ${BADGE_TF_LABELS[z.timeframe] ?? z.timeframe}`, yloc: 'price', style: 'label_left',
        color: col + 'CC', text_color: '#091019FF', size: 'tiny'},
    ]
  })
}
