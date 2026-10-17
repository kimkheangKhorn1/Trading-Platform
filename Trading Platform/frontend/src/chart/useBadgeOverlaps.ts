import { useEffect, useMemo, useRef, useState } from 'react'
import { BADGE_OVERLAP_DEFAULTS, badgeOverlapDrawings, fetchBadgeOverlaps, usableOverlapClock } from '../api/badgeOverlap'
import type { BadgeOverlapRequest, BadgeOverlapResult } from '../api/badgeOverlap'
import type { TimezoneId } from '../lib/format'
import type { DrawingObj, TimeframeCode } from '../api/types'
import type { IndicatorInstance } from '../state/indicators'

interface Job { uid: string; body: BadgeOverlapRequest }
interface State { key: string; scope: string; results: Record<string, BadgeOverlapResult>; error: string | null; loading: boolean }
export function useBadgeOverlaps(instances: IndicatorInstance[], feedId: string | null, tf: TimeframeCode,
  asOf: number | null, displayTime: number | null, step: number, precision: number, ready: boolean, timezone: TimezoneId = 'UTC') {
  const jobs: Job[] = ready && feedId && asOf !== null ? instances.flatMap(inst => {
    const c = inst.badgeOverlap ?? BADGE_OVERLAP_DEFAULTS
    if (inst.scriptId !== 'smc_ict_scalping_master' || !c.enabled || !inst.visible || !inst.timeframes.includes(tf)
      || inst.groupVisible.demand_supply === false) return []
    return [{uid: inst.uid, body: {feed_id: feedId, inputs: inst.inputs, as_of: asOf,
      timeframes: c.timeframes.filter(t => inst.timeframes.includes(t)),
      min_timeframes: c.minTimeframes, max_ranges: 8}}]
  }) : []
  const key = JSON.stringify({tf, jobs})
  // Everything except the clock: results from an earlier clock in the same scope stay on screen until the
  // next calculation lands, so the zones don't vanish and reappear on every replay step.
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
            for (const j of next.jobs) entries.push([j.uid, await fetchBadgeOverlaps(j.body)])
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
  const valid = state.key === key
  const sameScope = state.scope === scope
  const strongestKey = JSON.stringify(Object.fromEntries(instances.map(i => [i.uid, i.badgeOverlap?.strongestOnly ?? false])))
  const drawings = useMemo(() => {
    const out: Record<string, DrawingObj[]> = {}
    const strongest = JSON.parse(strongestKey) as Record<string, boolean>
    if (sameScope && displayTime !== null) for (const [uid, data] of Object.entries(state.results))
      if (usableOverlapClock(data, asOf))
        out[uid] = badgeOverlapDrawings(data, displayTime, step, precision, strongest[uid], timezone)
    return out
  }, [state, sameScope, displayTime, step, precision, asOf, strongestKey, timezone])
  return {drawings, displayedBands: Object.values(drawings).reduce((n, ds) => n + ds.filter(d => d.kind === 'box').length, 0), enabled: jobs.length > 0, loading: !valid || state.loading, error: valid ? state.error : null,
    warnings: valid ? Object.values(state.results).flatMap(d => d.warnings) : []}
}
