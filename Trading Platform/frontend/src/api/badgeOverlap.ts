import type { BarRow, DrawingObj, InputValue, TimeframeCode } from './types'
import { fmtDateTime } from '../lib/format'
import type { TimezoneId } from '../lib/format'

export const BADGE_TFS: TimeframeCode[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4']
/** 'ds': fresh Demand/Supply badges. 'ob': displayed order blocks (bullish = demand side, bearish = supply side). */
export type OverlapKind = 'ds' | 'ob'
export type ObType = 'swing' | 'internal'
export const OB_TYPES: ObType[] = ['swing', 'internal']
export interface BadgeOverlapSettings {
  enabled: boolean
  timeframes: TimeframeCode[]
  minTimeframes: number
  strongestOnly?: boolean
  candleReactions?: boolean
  /** Order block overlaps only */
  obTypes?: ObType[]
  freshOnly?: boolean
}
export const BADGE_OVERLAP_DEFAULTS: BadgeOverlapSettings = {
  enabled: false, timeframes: BADGE_TFS, minTimeframes: 2,
}
export const OB_OVERLAP_DEFAULTS: BadgeOverlapSettings = {
  enabled: false, timeframes: BADGE_TFS, minTimeframes: 2, obTypes: OB_TYPES, freshOnly: true,
}
/** Colors, names and drawing ids per overlap kind, so both layers can share one chart without colliding. */
export const OVERLAP_STYLE = {
  ds: {title: 'D/S overlap', long: 'Demand/Supply overlap', demand: '#22D3A5', supply: '#FF6B81',
    name: {demand: 'Demand', supply: 'Supply'}, group: 'badge_overlap', zoneId: -10000, reactionId: -30000},
  ob: {title: 'OB overlap', long: 'Order block overlap', demand: '#4F9DFF', supply: '#FFA24C',
    name: {demand: 'Bull OB', supply: 'Bear OB'}, group: 'ob_overlap', zoneId: -50000, reactionId: -70000},
} as const
export interface BadgeZone {
  timeframe: string; side: 'demand' | 'supply'; bottom: number; top: number; price: number; origin: number
  /** order blocks: structure-break candle that created the block, and swing / internal */
  born?: number; kind?: ObType
}
export interface OverlapReaction {
  time: number; confirmed_at: number; price: number; timeframe: 'M1' | 'M5'
  side: 'demand' | 'supply'; bottom: number; top: number
  lock_id: string; locked_at: number; touched_at: number
  code: string; name: string; bullish: boolean; neutral?: boolean; window_index?: number
  reaction: 'rejection' | 'continuation' | 'unconfirmed'; patterns: string[]
}
export interface BadgeOverlapResult {
  kind?: OverlapKind
  reactions?: OverlapReaction[]
  as_of: number
  lock_mode?: boolean
  active_locks?: number
  hidden_locks?: number
  deleted_locks?: number
  overlaps: { side: 'demand' | 'supply'; bottom: number; top: number; strength: number; timeframes: string[];
    zone_count: number; badges: BadgeZone[]; lock_id?: string; locked_at?: number }[]
  badges: BadgeZone[]
  sources: {timeframe: string; last_bar: number; badges: number}[]
  total_overlaps: number
  warnings: string[]
}
export interface BadgeOverlapRequest {
  feed_id: string; inputs: Record<string, InputValue>; as_of: number; timeframes: TimeframeCode[];
  min_timeframes: number; max_ranges: number; lock_id?: string; chart_timeframe?: TimeframeCode; strongest_only?: boolean; candle_reactions?: boolean
  kind?: OverlapKind; ob_types?: ObType[]; fresh_only?: boolean
}
export async function fetchBadgeOverlaps(body: BadgeOverlapRequest): Promise<BadgeOverlapResult> {
  const res = await fetch('/api/badge-overlaps', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Overlap request failed (${res.status})`)
  return data
}
export interface OverlapReactionResult {
  as_of: number
  reactions: OverlapReaction[]
  warnings: string[]
}
export async function fetchOverlapReactions(body: BadgeOverlapRequest): Promise<OverlapReactionResult> {
  const res = await fetch('/api/overlap-reactions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Candle reaction request failed (${res.status})`)
  return data
}
export const BADGE_TF_LABELS: Record<string, string> = {M1: '1m', M5: '5m', M15: '15m', M30: '30m', H1: '1H', H4: '4H'}
export type OverlapBand = BadgeOverlapResult['overlaps'][number]
export interface OverlapHover {
  kind?: OverlapKind
  band: OverlapBand
  asOf: number
  precision: number
  strongest: boolean
}

/** Never show a future snapshot after rewinding, including while an older request is in flight. */
export function usableOverlapClock(result: BadgeOverlapResult, asOf: number | null): boolean {
  return asOf !== null && result.as_of <= asOf
}

/** While the next snapshot is calculating, drop locks that chart bars opened after the snapshot have touched.
 * Only bars wholly after as_of count, so a lock is never hidden for a touch the server already ruled out. */
export function withoutTouchedLocks(result: BadgeOverlapResult, bars: BarRow[]): BadgeOverlapResult {
  const after = bars.filter(b => b[0] >= result.as_of)
  if (!result.lock_mode || !after.length) return result
  return {...result, overlaps: result.overlaps.filter(b =>
    !after.some(r => b.side === 'demand' ? r[3] <= b.top : r[2] >= b.bottom))}
}

export function badgeOverlapDrawings(data: BadgeOverlapResult, time: number, step: number, precision: number,
  strongestOnly = false, timezone: TimezoneId = 'UTC'): DrawingObj[] {
  const kind = data.kind ?? 'ds'
  const st = OVERLAP_STYLE[kind]
  const peaks = {demand: 0, supply: 0}
  for (const b of data.overlaps) peaks[b.side] = Math.max(peaks[b.side], b.strength)
  const zones = data.overlaps.flatMap((b, i): DrawingObj[] => {
    const strongest = b.strength === peaks[b.side]
    if (strongestOnly && !strongest && b.locked_at === undefined) return []
    const col = st[b.side]
    const f = (p: number) => p.toFixed(precision)
    const side = st.name[b.side]
    const title = `${strongest ? '★ ' : ''}${b.locked_at !== undefined ? 'Locked ' : ''}${side} · ${b.strength} timeframes · ${f(b.bottom)}–${f(b.top)}`
    // Short like the native price badges, so the chart's row packing never drags it away from its zone.
    const badge = `${strongest ? '★ ' : ''}${side} · ${b.strength} TF`
    const tip = kind === 'ob' ? `${side} overlap · ${b.strength} timeframes\nShared price range: ${f(b.bottom)} – ${f(b.top)}\nOrder blocks from ${b.timeframes.map(t => BADGE_TF_LABELS[t] ?? t).join(', ')} cover this entire band.\nSnapshot: ${fmtDateTime(data.as_of, timezone)}\nOrder block confluence only; this is not an entry confirmation.`
      : `${b.side === 'demand' ? 'Demand' : 'Supply'} overlap · Fresh at snapshot\nShared price range: ${f(b.bottom)} – ${f(b.top)}\n${b.strength} different timeframes cover this entire band: ${b.timeframes.map(t => BADGE_TF_LABELS[t] ?? t).join(', ')}.\nWidth: ${f(b.top - b.bottom)} price units.\nFresh means the source indicator has not marked the zone touched at its proximal level.\nSnapshot: ${fmtDateTime(data.as_of, timezone)} (${timezone === 'local' ? 'Local' : timezone})\nDemand/Supply confluence only; this is not an entry confirmation.`
    const lockTip = b.locked_at === undefined ? tip : `${title}\nLocked range: ${f(b.bottom)} – ${f(b.top)}\nLocked at: ${fmtDateTime(b.locked_at, timezone)}\nTimeframe count and source zones are frozen at locking.\nTouching this overlap’s own boundary deletes this group. All its original sources are retired from NEW overlaps; other already-locked overlaps remain unchanged until their own boundaries are touched. Distance only hides it temporarily.`
    const common = {group: st.group, created_at: b.locked_at ?? data.as_of, tooltip: lockTip,
      overlap: {kind, band: b, asOf: data.as_of, precision, strongest}}
    return [
      {...common, kind: 'box', id: st.zoneId - i * 2, left: time, right: time + 8 * step, top: b.top, bottom: b.bottom,
        border_color: col + 'FF', border_width: strongest ? 3 : 1, border_style: kind === 'ob' && !strongest ? 'dashed' : 'solid',
        bg_color: col + (strongest ? '40' : '18'), extend: 'right',
        text: '', text_color: '#FFFFFFFF', text_size: 'small', text_halign: 'right', text_valign: 'center'},
      {...common, kind: 'label', id: st.zoneId - 1 - i * 2, x: time + 2 * step, y: (b.top + b.bottom) / 2,
        text: badge, yloc: 'price', style: 'label_left', color: col + 'FF', text_color: '#091019FF', size: 'small'},
    ]
  })
  return [...zones, ...overlapReactionDrawings(data, precision, timezone, kind)]
}

/** Chart candles checked per lock, starting with the first-touch candle (same as the backend). */
export const REACTION_WINDOW: Record<string, number> = {M1: 10, M5: 3}

/** Reactions retain their historical location after the overlap is consumed. */
export function overlapReactionDrawings(data: Pick<BadgeOverlapResult,'as_of'|'reactions'>, precision: number, timezone: TimezoneId,
  kind: OverlapKind = 'ds'): DrawingObj[] {
  const st = OVERLAP_STYLE[kind]
  // Merge only equivalent reactions. Distinct zone roles, patterns and outcomes keep their own labels.
  const groups = new Map<string, OverlapReaction[]>()
  for (const r of data.reactions ?? []) {
    if (r.code === 'LLS') continue // Also suppress older in-flight/cached overlap confirmations.
    const step = r.timeframe === 'M1' ? 60 : 300
    const touch = r.touched_at - 60
    const start = touch - touch % step
    if (r.confirmed_at > data.as_of || r.time < r.locked_at || r.touched_at > data.as_of
      || r.confirmed_at !== r.time + step || r.time < start || r.time >= start + (REACTION_WINDOW[r.timeframe] ?? 3) * step) continue
    const key = `${r.timeframe}:${r.time}:${r.side}:${r.bullish}:${r.neutral ?? false}:${r.reaction}:${r.code}`
    groups.set(key, [...(groups.get(key) ?? []), r])
  }
  return [...groups.values()].map((rs, i) => {
    const r = rs[0]
    const state = r.reaction
    const symbol = state === 'rejection' ? '↶' : state === 'continuation' ? '➜' : '?'
    const color = state === 'unconfirmed' ? '#B58A24FF' : r.bullish ? '#089981FF' : '#C9364AFF'
    const detail = rs.map(x => `${st.name[x.side]} overlap: ${x.bottom.toFixed(precision)}–${x.top.toFixed(precision)}
${x.reaction === 'continuation' ? 'Continuation / break through the overlap' : x.reaction === 'rejection' ? 'Rejection away from the overlap' : 'Pattern present; reaction unconfirmed'}
Patterns: ${x.patterns.join(', ')}
Locked: ${fmtDateTime(x.locked_at, timezone)}
First touch confirmed: ${fmtDateTime(x.touched_at, timezone)}
Reaction candle: ${fmtDateTime(x.time, timezone)}
Candle ${Math.floor(x.time / (x.timeframe === 'M1' ? 60 : 300)) - Math.floor((x.touched_at - 60) / (x.timeframe === 'M1' ? 60 : 300)) + 1} of ${REACTION_WINDOW[x.timeframe] ?? 3} · first-touch candle + next ${(REACTION_WINDOW[x.timeframe] ?? 3) - 1} candles`).join('\n\n')
    return {kind: 'label', id: st.reactionId-i, group: st.group, created_at: r.confirmed_at,
      x: r.time, y: r.price, yloc: r.bullish ? 'belowbar' : 'abovebar', style: r.bullish ? 'label_up' : 'label_down',
      text: `${symbol} ${r.code === 'BE' ? (r.bullish ? 'Bull Eng' : 'Bear Eng') : r.code}`, color, text_color: '#FFFFFFFF', size: 'tiny',
      tooltip: `${BADGE_TF_LABELS[r.timeframe]} ${r.neutral ? 'Neutral' : r.bullish ? 'Bullish' : 'Bearish'} candle pattern · ${state}
${r.name}
${detail}
Confirmed at candle close: ${fmtDateTime(r.confirmed_at, timezone)}
Uses your enabled candlestick patterns and filters. This symbol describes the reaction at the original locked overlap, not an automatic entry.`}
  })
}
