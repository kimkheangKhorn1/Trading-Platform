import { useEffect, useMemo, useRef, useState } from 'react'
import { OB_TRADE_DEFAULTS, fetchObSignals } from '../api/obSignals'
import type { ObSignalRequest, ObSignalResult, ObTradeSettings } from '../api/obSignals'
import type { BarRow, TimeframeCode } from '../api/types'
import type { IndicatorInstance } from '../state/indicators'
import { SIGNAL_BUFFER, SIGNAL_MAX_RISK } from './overlapSignals'
import { ReactionQueue, confirmedCandleKey } from './reactionQueue'

/** The order block trade settings of an indicator instance. */
export function obTradeSettings(inst: IndicatorInstance): ObTradeSettings {
  return {...OB_TRADE_DEFAULTS, ...inst.obTrade}
}

interface Job { uid: string; body: ObSignalRequest }

/** Order block trade signals (5m chart) of every SMC instance whose OB trade is on, refreshed on each candle close
 *  and replay step. One request in flight; only the newest clock waits behind it. */
export function useObSignals(instances: IndicatorInstance[], feedId: string | null, tf: TimeframeCode,
  asOf: number | null, step: number, ready: boolean, bars: BarRow[] = [], replayStart: number | null = null) {
  const jobs: Job[] = ready && feedId && asOf !== null && tf === 'M5' ? instances.flatMap(inst => {
    const c = obTradeSettings(inst)
    if (inst.scriptId !== 'smc_ict_scalping_master' || !c.enabled || !inst.visible || !inst.timeframes.includes(tf)
      || !c.timeframes.length || !c.types.length) return []
    const inputs = Object.fromEntries(Object.entries(inst.inputs).filter(([k]) => k !== 'easy_mode'))
    return [{uid: inst.uid, body: {feed_id: feedId, inputs, as_of: asOf, timeframes: c.timeframes,
      confirmations: c.confirmations, ob_types: c.types, chart_timeframe: tf,
      signal_buffer: SIGNAL_BUFFER, signal_max_risk: SIGNAL_MAX_RISK, replay_start: replayStart}}]
  }) : []
  const scope = JSON.stringify(jobs.map(j => ({uid: j.uid, body: {...j.body, as_of: null}})))
  // a forming->closed update or OHLC correction can arrive with the same clock
  const key = JSON.stringify({jobs, close: confirmedCandleKey(bars, asOf, step)})
  const mounted = useRef(true)
  const current = useRef(scope)
  current.current = scope
  const [state, setState] = useState<{scope: string; results: Record<string, ObSignalResult>; error: string | null}>(
    {scope: '', results: {}, error: null})
  const queue = useRef<ReactionQueue<ObSignalRequest, ObSignalResult> | null>(null)
  if (!queue.current) queue.current = new ReactionQueue<ObSignalRequest, ObSignalResult>(fetchObSignals, (request, results, error) => {
    if (mounted.current && current.current === request.scope) setState({scope: request.scope, results, error})
  })
  useEffect(() => { mounted.current = true; return () => {mounted.current = false} }, [])
  useEffect(() => {
    const parsed = JSON.parse(key) as {jobs: Job[]}
    queue.current!.request({key, scope, jobs: parsed.jobs})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  // never show a result from a later clock after a rewind
  const results = useMemo(() => {
    const out: Record<string, ObSignalResult> = {}
    if (state.scope === scope && asOf !== null)
      for (const [uid, data] of Object.entries(state.results)) if (data.as_of <= asOf) out[uid] = data
    return out
  }, [state, scope, asOf])
  // per instance: the backend's warnings, or the request error (shown in the legend)
  const warnings = useMemo(() => {
    const out: Record<string, string[]> = {}
    for (const j of jobs) {
      const w = [...(results[j.uid]?.warnings ?? []), ...(state.scope === scope && state.error ? [state.error] : [])]
      if (w.length) out[j.uid] = w.map(x => `OB trade: ${x}`)
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results, state, scope])
  return {results, warnings, enabled: jobs.length > 0}
}
