import type { DrawingObj, InputValue, TimeframeCode } from './types'
import { fmtDateTime } from '../lib/format'
import type { TimezoneId } from '../lib/format'

export const BADGE_TFS: TimeframeCode[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4']
export interface BadgeOverlapSettings {
  enabled: boolean
  timeframes: TimeframeCode[]
  minTimeframes: number
  strongestOnly?: boolean
}
export const BADGE_OVERLAP_DEFAULTS: BadgeOverlapSettings = {
  enabled: false, timeframes: BADGE_TFS, minTimeframes: 2,
}
export interface BadgeZone {
  timeframe: string; side: 'demand' | 'supply'; bottom: number; top: number; price: number; origin: number
}
export interface BadgeOverlapResult {
  as_of: number
  overlaps: { side: 'demand' | 'supply'; bottom: number; top: number; strength: number; timeframes: string[];
    zone_count: number; badges: BadgeZone[] }[]
  badges: BadgeZone[]
  sources: {timeframe: string; last_bar: number; badges: number}[]
  total_overlaps: number
  warnings: string[]
}
export interface BadgeOverlapRequest {
  feed_id: string; inputs: Record<string, InputValue>; as_of: number; timeframes: TimeframeCode[];
  min_timeframes: number; max_ranges: number
}
export async function fetchBadgeOverlaps(body: BadgeOverlapRequest): Promise<BadgeOverlapResult> {
  const res = await fetch('/api/badge-overlaps', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Overlap request failed (${res.status})`)
  return data
}
export const BADGE_TF_LABELS: Record<string, string> = {M1: '1m', M5: '5m', M15: '15m', M30: '30m', H1: '1H', H4: '4H'}
export type OverlapBand = BadgeOverlapResult['overlaps'][number]
export interface OverlapHover {
  band: OverlapBand
  asOf: number
  precision: number
  strongest: boolean
}

/** Never show a future snapshot after rewinding, including while an older request is in flight. */
export function usableOverlapClock(result: BadgeOverlapResult, asOf: number | null): boolean {
  return asOf !== null && result.as_of <= asOf
}

export function badgeOverlapDrawings(data: BadgeOverlapResult, time: number, step: number, precision: number,
  strongestOnly = false, timezone: TimezoneId = 'UTC'): DrawingObj[] {
  const peaks = {demand: 0, supply: 0}
  for (const b of data.overlaps) peaks[b.side] = Math.max(peaks[b.side], b.strength)
  return data.overlaps.flatMap((b, i): DrawingObj[] => {
    const strongest = b.strength === peaks[b.side]
    if (strongestOnly && !strongest) return []
    const col = b.side === 'demand' ? '#22D3A5' : '#FF6B81'
    const f = (p: number) => p.toFixed(precision)
    const title = `${strongest ? '★ ' : ''}${b.side === 'demand' ? 'Demand' : 'Supply'} · ${b.strength} timeframes · ${f(b.bottom)}–${f(b.top)}`
    const tip = `${b.side === 'demand' ? 'Demand' : 'Supply'} overlap · Fresh at snapshot\nShared price range: ${f(b.bottom)} – ${f(b.top)}\n${b.strength} different timeframes cover this entire band: ${b.timeframes.map(t => BADGE_TF_LABELS[t] ?? t).join(', ')}.\nWidth: ${f(b.top - b.bottom)} price units.\nFresh means the source indicator has not marked the zone touched at its proximal level.\nSnapshot: ${fmtDateTime(data.as_of, timezone)} (${timezone === 'local' ? 'Local' : timezone})\nDemand/Supply confluence only; this is not an entry confirmation.`
    const common = {group: 'badge_overlap', created_at: data.as_of, tooltip: tip,
      overlap: {band: b, asOf: data.as_of, precision, strongest}}
    return [
      {...common, kind: 'box', id: -10000 - i * 2, left: time, right: time + 8 * step, top: b.top, bottom: b.bottom,
        border_color: col + 'FF', border_width: strongest ? 3 : 1, border_style: 'solid',
        bg_color: col + (strongest ? '40' : '18'), extend: 'right',
        text: '', text_color: '#FFFFFFFF', text_size: 'small', text_halign: 'right', text_valign: 'center'},
      {...common, kind: 'label', id: -10001 - i * 2, x: time + 2 * step, y: (b.top + b.bottom) / 2,
        text: title, yloc: 'price', style: 'label_left', color: col + 'FF', text_color: '#091019FF', size: 'small'},
    ]
  })
}
