import { useEffect, useMemo, useRef, useState } from 'react'
import { LiveSocket, nonStreamingStatus } from '../api/live'
import type { IndicatorOutput, IndicatorTable, TradeDto } from '../api/types'
import { Icon, Spinner } from '../components/common/Icon'
import { TF_SECONDS, useApp } from '../state/app'
import { useIndicators } from '../state/indicators'
import { useReplay } from '../state/replay'
import { useScale } from '../state/scale'
import { useTester } from '../state/tester'
import { ChartController } from './controller'
import { Legend } from './Legend'
import type { HitInfo } from './overlay'
import { ReplayBar } from './ReplayBar'
import { Ruler } from './Ruler'
import { IndicatorScheduler } from './scheduler'
import { IndicatorTables } from './Tables'
import { indexAtOrBefore } from './timeIndex'
import { useBadgeOverlaps } from './useBadgeOverlaps'
import { withOverlapDrawings } from './overlapOutput'
import { OB_SIGNALS, overlapSignalDrawings, overlapSignalTable, withoutRepeatedReactions } from './overlapSignals'
import { useObSignals, obTradeSettings } from './useObSignals'
import { obZoneDrawings } from '../api/obSignals'
import { ChartTooltip } from './OverlapTooltip'
import { IndicatorPanels } from './ConfluencePanel'
import { barCloseTime } from './replayTime'

const LIVE_REFRESH_MS = 3000

export function ChartView() {
  const host = useRef<HTMLDivElement>(null)
  const ctrlRef = useRef<ChartController | null>(null)
  const sched = useRef(new IndicatorScheduler())
  const runScope = useRef('')
  const [ctrl, setCtrl] = useState<ChartController | null>(null)
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const [tip, setTip] = useState<{ info: HitInfo; x: number; y: number; pinned?: boolean } | null>(null)
  const [version, setVersion] = useState(0)
  const [liveTick, setLiveTick] = useState(0)
  const lastLiveRefresh = useRef(0)

  const feedId = useApp((s) => s.feedId)
  const feed = useApp((s) => s.feeds.find((f) => f.id === feedId))
  const tf = useApp((s) => s.timeframe)
  const tz = useApp((s) => s.timezone)
  const focus = useApp((s) => s.focus)
  const notice = useApp((s) => s.chartNotice)
  const scripts = useApp((s) => s.scripts)
  const instances = useIndicators((s) => s.instances)
  const outputs = useIndicators((s) => s.outputs)
  const result = useTester((s) => s.result)
  const selectedTradeId = useTester((s) => s.selectedTradeId)
  const showTrades = useTester((s) => s.showTradesOnChart)
  const replayMode = useReplay((s) => s.mode)
  const cursorTime = useReplay((s) => s.cursorTime)
  const replayStart = useReplay((s) => s.startTime)
  const scaleAuto = useScale((s) => s.auto)
  const scaleKind = useScale((s) => s.kind)
  const resetSeq = useScale((s) => s.resetSeq)

  // ------------------------------------------------------------- chart + live socket
  useEffect(() => {
    const c = new ChartController(host.current!, {
      onCrosshair: (i) => setHoverIdx(i),
      onHover: (info, x, y) => setTip(current => current?.pinned ? current : info ? { info, x, y } : null),
      onClick: (info, time) => {
        const rp = useReplay.getState()
        if (rp.mode === 'picking' && time !== null) {
          const idx = indexAtOrBefore(c.bars.map((b) => b[0]), time)
          if (idx >= 0) {
            c.startReplay(idx)
            rp.set({ mode: 'active', playing: false, cursorTime: c.bars[idx][0], cutoffTime: c.replayCutoffTime(),
              startTime: c.bars[idx][0] })
          }
          return
        }
        if (info?.overlap) {
          setTip(current => ({info, x: current?.x ?? 16, y: current?.y ?? 110, pinned: true}))
          return
        }
        setTip(null)
        if (info?.tradeId) {
          const t = useTester.getState().result?.trades.find((x) => x.id === info.tradeId)
          if (t) useTester.getState().selectTrade(t)
        }
      },
      onData: () => setVersion((v) => v + 1),
      onNotice: (m) => useApp.getState().setChartNotice(m),
    })
    ctrlRef.current = c
    setCtrl(c)
    if (import.meta.env.DEV) (window as unknown as { __ctrl: ChartController }).__ctrl = c // debugging aid
    return () => {
      c.dispose()
      ctrlRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!ctrl || !feedId || !feed) return
    const stored = nonStreamingStatus(feed)
    if (stored) {
      useApp.getState().setLive(stored.state, stored.message)
      return
    }
    useApp.getState().setLive('connecting', 'Connecting to live market data')
    const live = new LiveSocket()
    live.onStatus = (state, msg) => useApp.getState().setLive(state, msg)
    live.onBars = (fid, t, rows) => {
      if (fid !== ctrl.feedId || t !== ctrl.tf) return
      const structural = ctrl.applyLive(rows)
      const now = Date.now()
      if (structural || now - lastLiveRefresh.current > LIVE_REFRESH_MS) {
        lastLiveRefresh.current = now
        setLiveTick((k) => k + 1)
      }
    }
    live.subscribe(feedId, tf)
    live.connect()
    return () => live.close()
    // Feed objects can refresh while their streaming capability stays the same.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctrl, feedId, tf, feed?.provider, feed?.live])

  useEffect(() => {
    if (!ctrl || !feedId) return
    const rp = useReplay.getState()
    const sameFeed = ctrl.feedId === feedId
    const cutoff = sameFeed && rp.mode !== 'off' ? rp.cutoffTime : null
    if (!sameFeed) rp.set({ mode: 'off', playing: false, cursorTime: null, cutoffTime: null, startTime: null })
    else rp.set({ playing: false })
    for (const inst of useIndicators.getState().instances) sched.current.forget(inst.uid)
    useIndicators.setState({ outputs: {} }) // outputs belong to the previous feed/timeframe
    void ctrl.load(feedId, tf, cutoff).then((loaded) => {
      const current = useReplay.getState()
      if (loaded && cutoff !== null && current.mode !== 'off' && current.cutoffTime === cutoff) {
        current.set({ cursorTime: ctrl.replayCursorTime() })
      }
    })
  }, [ctrl, feedId, tf])

  useEffect(() => ctrl?.setTimezone(tz), [ctrl, tz])

  // ---------------------------------------------------------------- indicator runs
  const firstTime = ctrl && ctrl.bars.length ? ctrl.bars[0][0] : null
  // also while picking another start bar mid-replay: the bars after the cursor stay hidden until a bar is picked
  const replayEnd = replayMode !== 'off' ? ctrl?.replayCursorTime() ?? null : null
  // A historical navigation window must not calculate millions of later bars.
  const indicatorEnd = replayEnd ?? (ctrl?.hasNewer ? ctrl.bars.at(-1)?.[0] ?? null : null)
  // saved instances of scripts that no longer exist are pruned by syncWithMeta; never request them
  const known = useMemo(() => new Set(scripts.map((s) => s.id)), [scripts])
  useEffect(() => {
    // only once the controller holds candles of this feed/timeframe (a switch is still loading otherwise)
    if (!ctrl || ctrl.loading || !feedId || firstTime === null || ctrl.feedId !== feedId || ctrl.tf !== tf || !ctrl.bars.length) return
    if (replayMode !== 'off' && replayEnd === null) return
    const scope = JSON.stringify({feedId, tf, firstTime})
    const changedChart = runScope.current !== scope
    runScope.current = scope
    for (const inst of instances) {
      if (!inst.visible || !inst.timeframes.includes(tf) || !known.has(inst.scriptId)) continue
      sched.current.request(inst.uid, {
        feedId, tf, scriptId: inst.scriptId, inputs: inst.inputs, start: firstTime, end: indicatorEnd,
        replayStart: replayEnd !== null ? replayStart : null,
      }, replayEnd !== null || changedChart ? 0 : 250)
    }
  }, [ctrl, feedId, tf, firstTime, replayEnd, indicatorEnd, replayMode, cursorTime, replayStart, instances, known, version])

  useEffect(() => {
    if (!ctrl || ctrl.loading || !feedId || firstTime === null || liveTick === 0 || replayMode !== 'off') return
    if (ctrl.feedId !== feedId || ctrl.tf !== tf || !ctrl.bars.length) return
    for (const inst of instances) {
      if (!inst.visible || !inst.timeframes.includes(tf) || !known.has(inst.scriptId)) continue
      sched.current.request(inst.uid, {
        feedId, tf, scriptId: inst.scriptId, inputs: inst.inputs, start: firstTime, end: indicatorEnd,
      }, 0, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveTick])

  useEffect(() => {
    const alive = new Set(instances.map((i) => i.uid))
    for (const uid of Object.keys(outputs)) if (!alive.has(uid)) sched.current.forget(uid)
  }, [instances, outputs])

  // ----------------------------------------------------------- render outputs/trades
  const badgeTip = ctrl?.shownBars.at(-1)
  const badgeReady = !!ctrl && !ctrl.loading && ctrl.feedId === feedId && ctrl.tf === tf
  // While picking a replay start bar there is no cutoff yet: keep the clock already on screen, so overlap
  // zones and their candle-reaction symbols don't vanish until a bar is chosen.
  const replayCutoff = replayMode !== 'off' ? ctrl?.replayCutoffTime() ?? null : null
  const badgeClock = badgeTip ? (replayCutoff !== null ? replayCutoff
    : replayMode === 'active' ? null
    : badgeTip[6] ? barCloseTime(badgeTip[0], tf, feed?.alignment)
    : Math.min(Math.floor(Date.now() / 60000) * 60, barCloseTime(badgeTip[0], tf, feed?.alignment))) : null
  // the replay's trade record starts at its start bar (the backend computes it from there, never from its cache)
  const recordStart = replayMode !== 'off' ? replayStart : null
  const badgeOverlap = useBadgeOverlaps(instances, feedId, tf, badgeClock, badgeTip?.[0] ?? null,
    TF_SECONDS[tf], ctrl?.precision ?? 3, badgeReady, tz, ctrl?.shownBars, recordStart)
  // order block trade signals (5m chart), next to the D/S overlap ones
  const obSignals = useObSignals(instances, feedId, tf, badgeClock, TF_SECONDS[tf], badgeReady, ctrl?.shownBars, recordStart)
  useEffect(() => { setTip(null) }, [badgeClock, feedId, tf, instances])
  useEffect(() => {
    if (!tip?.pinned) return
    const close = (e: KeyboardEvent) => { if (e.key === 'Escape') setTip(null) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [tip?.pinned])
  const tipTime = badgeTip?.[0] ?? null
  const outData = useMemo(() => {
    const o: Record<string, IndicatorOutput | null> = {}
    for (const inst of instances) {
      // Overlap reversal signals dashboard (5m) when the Trade switch is on.
      // Replay only: the record of the trades signalled after the replay start bar
      const trades = badgeOverlap.signals[inst.uid]
      const table = trades && replayMode === 'active' && replayStart !== null
        ? overlapSignalTable(trades, feed?.symbol ?? '', '5m', ctrl?.precision ?? 3, tz, badgeClock, replayStart) : null
      // One label per signal; reaction symbols the labels repeat are dropped
      // Trade plans (entry / SL / TP boxes) are a replay tool: every trade since the start bar keeps its plan until
      // the replay is exited; outside replay only the BUY / SELL labels are drawn
      const signals = overlapSignalDrawings(trades ?? [], ctrl?.precision ?? 3, tz, tipTime, TF_SECONDS[tf],
        replayMode === 'off' ? 'none' : replayStart !== null ? {since: replayStart} : 'newest')
      // Order block trade: its own labels, plans and dashboard (one OB trade at a time, apart from D/S)
      const ob = obSignals.results[inst.uid]
      const obTable = ob && replayMode === 'active' && replayStart !== null
        ? overlapSignalTable(ob.signals, feed?.symbol ?? '', '5m', ctrl?.precision ?? 3, tz, badgeClock, replayStart, OB_SIGNALS) : null
      const obDrawn = ob ? [
        ...(obTradeSettings(inst).zones ?? true) && tipTime !== null
          ? obZoneDrawings(ob.zones, tipTime, TF_SECONDS[tf], ctrl?.precision ?? 3, tz) : [],
        ...overlapSignalDrawings(ob.signals, ctrl?.precision ?? 3, tz, tipTime, TF_SECONDS[tf],
          replayMode === 'off' ? 'none' : replayStart !== null ? {since: replayStart} : 'newest', OB_SIGNALS),
      ] : []
      const overlaps = withoutRepeatedReactions(badgeOverlap.drawings[inst.uid] ?? [], signals)
      const out = withOverlapDrawings(inst,outputs[inst.uid]?.data??null, [...overlaps, ...signals, ...obDrawn],
        [...(table ? [table] : []), ...(obTable ? [obTable] : [])])
      const obWarnings = obSignals.warnings[inst.uid] ?? []
      o[inst.uid] = out && obWarnings.length ? {...out, warnings: [...out.warnings, ...obWarnings]} : out
    }
    return o
  }, [instances, outputs, badgeOverlap.drawings, badgeOverlap.signals, obSignals.results, obSignals.warnings, feed?.symbol, ctrl, tz, badgeClock, replayMode, replayStart, tipTime, tf])

  useEffect(() => {
    if (ctrl) ctrl.setIndicators(instances, outData, scripts)
  }, [ctrl, instances, outData, scripts, version])

  const tables = useMemo(() => {
    const list: IndicatorTable[] = []
    for (const inst of instances) {
      if (inst.visible && inst.timeframes.includes(tf)) list.push(...(outData[inst.uid]?.tables ?? []))
    }
    return list
  }, [instances, outData, tf])

  const panels = useMemo(() => instances.flatMap((inst) => (inst.visible && inst.timeframes.includes(tf)
    ? (outData[inst.uid]?.panels ?? []).map((panel) => ({ uid: inst.uid, panel })) : [])), [instances, outData, tf])

  useEffect(() => {
    if (!ctrl) return
    const matches = result && result.request.feed_id === feedId && result.request.timeframe === tf
    if (!matches) return ctrl.setTrades(null)
    let trades: TradeDto[] = result.trades
    if (replayMode !== 'off' && cursorTime !== null) {
      // never reveal fills that happen after the replay cursor
      trades = trades
        .filter((t) => t.entry_time <= cursorTime)
        .map((t) => (t.exit_time !== null && t.exit_time > cursorTime
          ? { ...t, fills: t.fills.filter((f) => f.time <= cursorTime), exit_time: null, exit_price: null, exit_reason: 'Open' }
          : t))
    }
    ctrl.setTrades({
      trades,
      selectedId: selectedTradeId,
      showAll: showTrades,
      strategyTitles: Object.fromEntries(result.strategies.map((s) => [s.id, s.title])),
    })
  }, [ctrl, result, selectedTradeId, showTrades, feedId, tf, replayMode, cursorTime, version])

  useEffect(() => {
    if (ctrl && focus) void ctrl.focusRange(focus.from, focus.to)
  }, [ctrl, focus])

  // ------------------------------------------------------------- price scale (auto / log / %)
  useEffect(() => {
    // a new symbol or timeframe starts auto-scaled, so its candles are never left off-screen
    useScale.getState().set({ auto: true })
  }, [feedId, tf])
  useEffect(() => {
    ctrl?.setPriceScale(scaleAuto, scaleKind)
  }, [ctrl, scaleAuto, scaleKind])
  useEffect(() => {
    if (ctrl && resetSeq) ctrl.resetScale()
  }, [ctrl, resetSeq])
  useEffect(() => {
    if (!ctrl) return
    const el = host.current!
    // dragging/stretching the price axis switches auto-scale off inside the chart: mirror it on the button
    const sync = () => requestAnimationFrame(() => {
      const auto = ctrl.isAutoScale()
      if (auto !== useScale.getState().auto) useScale.getState().set({ auto })
    })
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.altKey && e.code === 'KeyR' && !t.closest('input, textarea, [role=dialog]')) {
        e.preventDefault()
        useScale.getState().reset()
      }
    }
    el.addEventListener('pointerup', sync)
    el.addEventListener('dblclick', sync)
    el.addEventListener('wheel', sync, { passive: true })
    window.addEventListener('keydown', key)
    return () => {
      el.removeEventListener('pointerup', sync)
      el.removeEventListener('dblclick', sync)
      el.removeEventListener('wheel', sync)
      window.removeEventListener('keydown', key)
    }
  }, [ctrl])

  const loadingAny = Object.values(outputs).some((o) => o.loading)
  const picking = replayMode === 'picking'

  return (
    <div className={`chart-wrap ${picking ? 'picking' : ''}`}>
      <div ref={host} className="chart-host" />
      {ctrl ? <Legend ctrl={ctrl} hoverIdx={hoverIdx} version={version} /> : null}
      {ctrl ? <IndicatorTables ctrl={ctrl} tables={tables} version={version} /> : null}
      {ctrl ? <IndicatorPanels ctrl={ctrl} panels={panels} version={version} /> : null}
      {tip ? <ChartTooltip info={tip.info} x={tip.x} y={tip.y} pinned={tip.pinned} onClose={() => setTip(null)} /> : null}
      {notice ? (
        <div className="chart-notice">
          <Icon name="info" size={14} /> {notice}
        </div>
      ) : null}
      {picking ? <div className="replay-hint">Click a bar to start replay from it · Esc to cancel</div> : null}
      {loadingAny ? <div className="chart-busy"><Spinner size={12} /> calculating</div> : null}
      {ctrl ? <ReplayBar ctrl={ctrl} /> : null}
      {ctrl ? <Ruler ctrl={ctrl} /> : null}
    </div>
  )
}
