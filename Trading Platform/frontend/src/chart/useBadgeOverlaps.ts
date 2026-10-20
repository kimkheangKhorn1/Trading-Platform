import { useEffect, useMemo, useRef, useState } from 'react'
import { BADGE_OVERLAP_DEFAULTS, badgeOverlapDrawings, fetchBadgeOverlaps, usableOverlapClock, withoutTouchedLocks, fetchOverlapReactions, overlapReactionDrawings } from '../api/badgeOverlap'
import type { BadgeOverlapRequest, BadgeOverlapResult, BadgeOverlapSettings, OverlapReactionResult, OverlapTrade } from '../api/badgeOverlap'
import type { TimezoneId } from '../lib/format'
import type { BarRow, DrawingObj, TimeframeCode } from '../api/types'
import type { IndicatorInstance } from '../state/indicators'

import { ReactionQueue, confirmedCandleKey } from './reactionQueue'
import { SIGNAL_BUFFER, SIGNAL_MAX_RISK } from './overlapSignals'

interface Job { uid: string; body: BadgeOverlapRequest }
/** The overlap settings of an indicator instance. */
export function overlapSettings(inst: IndicatorInstance): BadgeOverlapSettings {
  return inst.badgeOverlap ?? BADGE_OVERLAP_DEFAULTS
}
/** Settings that only change what the indicator draws (Easy Mode) are not overlap settings: flipping them must not
 *  restart or clear the overlap scan. */
const DISPLAY_ONLY_INPUTS = ['easy_mode']
function overlapInputs(inputs: IndicatorInstance['inputs']): IndicatorInstance['inputs'] {
  return Object.fromEntries(Object.entries(inputs).filter(([k]) => !DISPLAY_ONLY_INPUTS.includes(k)))
}
/** The Trade switch: on by default. */
export function signalsOn(c: BadgeOverlapSettings): boolean {
  return c.signals ?? true
}
interface State { key: string; scope: string; results: Record<string, BadgeOverlapResult>; error: string | null; loading: boolean }
export function useBadgeOverlaps(instances: IndicatorInstance[], feedId: string | null, tf: TimeframeCode,
  asOf: number | null, displayTime: number | null, step: number, precision: number, ready: boolean, timezone: TimezoneId = 'UTC',
  bars: BarRow[] = [], replayStart: number | null = null) {
  const jobs: Job[] = ready && feedId && asOf !== null ? instances.flatMap(inst => {
    const c = overlapSettings(inst)
    if (inst.scriptId !== 'smc_ict_scalping_master' || !c.enabled || !inst.visible || !inst.timeframes.includes(tf)
      || inst.groupVisible.demand_supply === false) return []
    // Reversal trade signals when the Trade switch is on (5m chart)
    const signals = tf === 'M5' && signalsOn(c)
    return [{uid: inst.uid, body: {feed_id: feedId, inputs: overlapInputs(inst.inputs), as_of: asOf,
      timeframes: c.timeframes.filter(t => inst.timeframes.includes(t)),
      min_timeframes: c.minTimeframes, max_ranges: 8, lock_id: inst.uid, chart_timeframe: tf, strongest_only: c.strongestOnly ?? false, candle_reactions: c.candleReactions ?? true,
      // reversal trade signals: 5m chart only
      signals, signal_buffer: SIGNAL_BUFFER, signal_max_risk: SIGNAL_MAX_RISK}}]
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
            for (const j of next.jobs) entries.push([j.uid, await fetchBadgeOverlaps({...j.body, candle_reactions: false, signals: false})])
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
  const reactionKey = JSON.stringify({key,closeKey,discovery:state.scope===scope ? state.key : '',replayStart})
  useEffect(() => {
    const parsed = JSON.parse(key) as {jobs:Job[]}
    const jobs = (tf==='M1' || tf==='M5') ? parsed.jobs.filter(j => j.body.candle_reactions || j.body.signals)
      .map(j => ({...j, body: {...j.body, replay_start: replayStart}})) : []
    reactionQueue.current!.request({key:reactionKey,scope,jobs})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reactionKey])
  const valid = state.key === key
  const sameScope = state.scope === scope
  // Primitive key: shownBars is a fresh array every render, and only the few bars after the shown snapshot matter.
  const since = Math.min(...Object.values(state.results).map(d => d.as_of))
  const recentKey = valid ? '' : JSON.stringify(bars.filter(b => b[0] >= since))
  const strongestKey = JSON.stringify(Object.fromEntries(instances.map(i => [i.uid, overlapSettings(i).strongestOnly ?? false])))
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
        out[uid]=[...(out[uid]??[]),...overlapReactionDrawings(data,precision,timezone)]
    // trade signals are drawn by the chart (overlapSignalDrawings)
    }
    return out
  }, [reactions, scope, state, sameScope, displayTime, step, precision, asOf, strongestKey, timezone, recentKey])
  // Trades of the reversal signals per instance (only instances whose request asked for them), for the dashboard
  const signals = useMemo(() => {
    const out: Record<string, OverlapTrade[]> = {}
    if (reactions.scope!==scope) return out
    const asked = new Set((JSON.parse(scope) as {jobs:Job[]}).jobs.filter(j => j.body.signals).map(j => j.uid))
    for (const [uid,data] of Object.entries(reactions.results))
      if (asked.has(uid) && asOf!==null && data.as_of<=asOf) out[uid]=data.signals??[]
    return out
  }, [reactions, scope, asOf])
  const hiddenLocks = sameScope ? Object.values(state.results).reduce((n, d) => n + (d.hidden_locks ?? 0), 0) : 0
  return {drawings, signals, hiddenLocks, displayedBands: Object.values(drawings).reduce((n, ds) => n + ds.filter(d => d.kind === 'box').length, 0), enabled: jobs.length > 0, loading: !valid || state.loading, error: valid ? state.error : null,
    warnings: [...(valid ? Object.values(state.results).flatMap(d => d.warnings) : []),
      ...(reactions.scope===scope && reactions.error ? [`Candle reactions: ${reactions.error}`] : [])]}
}
