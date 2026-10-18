import { useEffect, useMemo, useRef, useState } from 'react'
import { BADGE_OVERLAP_DEFAULTS, OB_OVERLAP_DEFAULTS, OB_TYPES, badgeOverlapDrawings, fetchBadgeOverlaps, usableOverlapClock, withoutTouchedLocks, fetchOverlapReactions, overlapReactionDrawings } from '../api/badgeOverlap'
import type { BadgeOverlapRequest, BadgeOverlapResult, BadgeOverlapSettings, OverlapKind, OverlapReactionResult } from '../api/badgeOverlap'
import type { TimezoneId } from '../lib/format'
import type { BarRow, DrawingObj, TimeframeCode } from '../api/types'
import type { IndicatorInstance } from '../state/indicators'

import { ReactionQueue, confirmedCandleKey } from './reactionQueue'

interface Job { uid: string; body: BadgeOverlapRequest }
/** The overlap settings of one kind on an indicator instance. */
export function overlapSettings(inst: IndicatorInstance, kind: OverlapKind): BadgeOverlapSettings {
  return kind === 'ob' ? inst.obOverlap ?? OB_OVERLAP_DEFAULTS : inst.badgeOverlap ?? BADGE_OVERLAP_DEFAULTS
}
/** Settings that only change what the indicator draws (Easy Mode) are not overlap settings: flipping them must not
 *  restart or clear the overlap scan. */
const DISPLAY_ONLY_INPUTS = ['easy_mode']
function overlapInputs(inputs: IndicatorInstance['inputs']): IndicatorInstance['inputs'] {
  return Object.fromEntries(Object.entries(inputs).filter(([k]) => !DISPLAY_ONLY_INPUTS.includes(k)))
}
interface State { key: string; scope: string; results: Record<string, BadgeOverlapResult>; error: string | null; loading: boolean }
export function useBadgeOverlaps(instances: IndicatorInstance[], feedId: string | null, tf: TimeframeCode,
  asOf: number | null, displayTime: number | null, step: number, precision: number, ready: boolean, timezone: TimezoneId = 'UTC',
  bars: BarRow[] = [], kind: OverlapKind = 'ds') {
  const jobs: Job[] = ready && feedId && asOf !== null ? instances.flatMap(inst => {
    const c = overlapSettings(inst, kind)
    // Order block types follow the chart: a hidden swing/internal OB group is not a source.
    const obTypes = (c.obTypes ?? OB_TYPES).filter(t => inst.groupVisible[t === 'swing' ? 'swing_ob' : 'internal_ob'] !== false)
    if (inst.scriptId !== 'smc_ict_scalping_master' || !c.enabled || !inst.visible || !inst.timeframes.includes(tf)
      || (kind === 'ds' ? inst.groupVisible.demand_supply === false : !obTypes.length)) return []
    return [{uid: inst.uid, body: {feed_id: feedId, inputs: overlapInputs(inst.inputs), as_of: asOf,
      timeframes: c.timeframes.filter(t => inst.timeframes.includes(t)),
      min_timeframes: c.minTimeframes, max_ranges: 8, lock_id: inst.uid, chart_timeframe: tf, strongest_only: c.strongestOnly ?? false, candle_reactions: c.candleReactions ?? true,
      ...(kind === 'ob' ? {kind, ob_types: obTypes, fresh_only: c.freshOnly ?? true} : {})}}]
  }) : []
  const key = JSON.stringify({tf, jobs})
  // Everything except the clock identifies an analysis scope. While the next clock calculates, the previous
  // locks stay on screen (no blinking), minus any that newer chart bars have already touched.
  const scope = JSON.stringify({tf, jobs: jobs.map(j => ({uid: j.uid, body: {...j.body, as_of: null}}))})
  const current = useRef(scope)
  const pending = useRef<{key: string; scope: string; jobs: Job[]} | null>(null)
  const running = useRef(false)
  const mounted = useRef(true)
  const [state, setState] = useState<State>({key: '', scope: '', results: {}, error: null, loading: false})
  useEffect(() => { mounted.current = true; return () => {mounted.current = false} }, [])
  useEffect(() => {
    current.current = scope
    const job = JSON.parse(key) as {jobs: Job[]}
    pending.current = {key, scope, jobs: job.jobs}
    // One calculation in flight; while it runs, retain only the latest requested chart/replay clock. A result for
    // an older clock is still shown (it's newer than what's on screen) unless the scope itself changed.
    if (running.current) return
    running.current = true
    void (async () => {
      try {
        while (pending.current) {
          const next = pending.current
          pending.current = null
          try {
            const entries: [string, BadgeOverlapResult][] = []
            for (const j of next.jobs) entries.push([j.uid, await fetchBadgeOverlaps({...j.body, candle_reactions: false})])
            if (mounted.current && current.current === next.scope)
              setState({key: next.key, scope: next.scope, results: Object.fromEntries(entries), error: null, loading: false})
          } catch (e) {
            if (mounted.current && current.current === next.scope)
              setState({key: next.key, scope: next.scope, results: {}, error: e instanceof Error ? e.message : String(e), loading: false})
          }
        }
      } finally {running.current = false}
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  const [reactions, setReactions] = useState<{scope:string; results:Record<string,OverlapReactionResult>; error:string|null}>({scope:'',results:{},error:null})
  const reactionScope = useRef(scope)
  reactionScope.current = scope
  const reactionQueue = useRef<ReactionQueue | null>(null)
  if (!reactionQueue.current) reactionQueue.current = new ReactionQueue(fetchOverlapReactions, (request, results, error) => {
    if (mounted.current && reactionScope.current === request.scope) setReactions({scope:request.scope,results,error})
  })
  // A forming->closed update or OHLC correction can arrive with the same observation clock.
  // Include the most recent closed candle itself, rather than waiting for a minute-clock change.
  const closeKey = confirmedCandleKey(bars,asOf,step)
  // Source discovery can finish after the fast lane; immediately recheck any newly captured locks.
  const reactionKey = JSON.stringify({key,closeKey,discovery:state.scope===scope ? state.key : ''})
  useEffect(() => {
    const parsed = JSON.parse(key) as {jobs:Job[]}
    const jobs = (tf==='M1' || tf==='M5') ? parsed.jobs.filter(j => j.body.candle_reactions) : []
    reactionQueue.current!.request({key:reactionKey,scope,jobs})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reactionKey])
  const valid = state.key === key
  const sameScope = state.scope === scope
  // Primitive key: shownBars is a fresh array every render, and only the few bars after the shown snapshot matter.
  const since = Math.min(...Object.values(state.results).map(d => d.as_of))
  const recentKey = valid ? '' : JSON.stringify(bars.filter(b => b[0] >= since))
  const strongestKey = JSON.stringify(Object.fromEntries(instances.map(i => [i.uid, overlapSettings(i, kind).strongestOnly ?? false])))
  const drawings = useMemo(() => {
    const out: Record<string, DrawingObj[]> = {}
    const strongest = JSON.parse(strongestKey) as Record<string, boolean>
    const recent = recentKey ? JSON.parse(recentKey) as BarRow[] : []
    if (sameScope && displayTime !== null) for (const [uid, data] of Object.entries(state.results))
      if (usableOverlapClock(data, asOf))
        out[uid] = badgeOverlapDrawings(recent.length ? withoutTouchedLocks(data, recent) : data, displayTime, step, precision,
          strongest[uid], timezone)
    if (reactions.scope===scope) for (const [uid,data] of Object.entries(reactions.results)) {
      if (asOf!==null && data.as_of<=asOf)
        out[uid]=[...(out[uid]??[]),...overlapReactionDrawings(data,precision,timezone,kind)]
    }
    return out
  }, [reactions, scope, state, sameScope, displayTime, step, precision, asOf, strongestKey, timezone, recentKey, kind])
  const hiddenLocks = sameScope ? Object.values(state.results).reduce((n, d) => n + (d.hidden_locks ?? 0), 0) : 0
  return {drawings, hiddenLocks, displayedBands: Object.values(drawings).reduce((n, ds) => n + ds.filter(d => d.kind === 'box').length, 0), enabled: jobs.length > 0, loading: !valid || state.loading, error: valid ? state.error : null,
    warnings: [...(valid ? Object.values(state.results).flatMap(d => d.warnings) : []),
      ...(reactions.scope===scope && reactions.error ? [`Candle reactions: ${reactions.error}`] : [])]}
}
