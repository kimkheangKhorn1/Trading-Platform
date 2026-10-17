import { useCallback, useEffect, useState } from 'react'
import type { MouseEventParams, Time } from 'lightweight-charts'
import { Select } from '../components/common/Fields'
import { Icon } from '../components/common/Icon'
import { fmtDateTime } from '../lib/format'
import { useApp } from '../state/app'
import { useReplay } from '../state/replay'
import type { ChartController } from './controller'

const SPEEDS = [0.5, 1, 2, 3, 5, 10, 20]

/** TradingView replay shortcuts: Shift+↓ play/pause, Shift+→ step forward (and Shift+← back). */
function isTyping(t: EventTarget | null): boolean {
  return t instanceof HTMLElement && !!t.closest('input, textarea, select, [contenteditable=true], [role=dialog]')
}

/**
 * Bar replay: the chart only receives candles up to the cursor and indicators are recomputed by the
 * backend with ``end_time = cursor`` - later candles are never sent to the engine.
 */
export function ReplayBar({ ctrl }: { ctrl: ChartController }) {
  const { mode, playing, speed, smooth, cursorTime, cutoffTime, set } = useReplay()
  const tz = useApp((s) => s.timezone)
  const [cut, setCut] = useState<{ x: number; width: number; height: number } | null>(null)

  // indicators run up to the last completed bar, never the candle that is still forming
  const sync = useCallback(() => set({ cursorTime: ctrl.replayCursorTime(), cutoffTime: ctrl.replayCutoffTime() }), [ctrl, set])
  const step = () => { if (ctrl.stepReplay()) sync() }
  const back = () => { if (ctrl.stepBackReplay()) sync() }

  useEffect(() => {
    if (mode !== 'active' || !playing) return
    const barMs = 1000 / speed
    // smooth: about one update per 30 ms within each bar's time, so slow speeds show more movement
    const maxTicks = smooth ? Math.max(1, Math.min(60, Math.floor(barMs / 30))) : 1
    let timer: ReturnType<typeof setTimeout>
    const tick = () => {
      const r = ctrl.advanceReplay(maxTicks)
      if (r === 'end') return set({ playing: false })
      if (r === 'bar') set({ cursorTime: ctrl.replayCursorTime(), cutoffTime: ctrl.replayCutoffTime() })
      timer = setTimeout(tick, r === 'loading' ? 100 : maxTicks > 1 ? barMs / ctrl.replayTickCount() : barMs)
    }
    timer = setTimeout(tick, maxTicks > 1 ? barMs / maxTicks : barMs)
    return () => clearTimeout(timer)
  }, [mode, playing, speed, smooth, ctrl, set])

  // keyboard shortcuts while replaying
  useEffect(() => {
    if (mode !== 'active') return
    const onKey = (e: KeyboardEvent) => {
      if (!e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || isTyping(e.target)) return
      const st = useReplay.getState()
      if (ctrl.loading) return
      if (e.key === 'ArrowDown') set({ playing: !st.playing })
      else if (e.key === 'ArrowRight') { set({ playing: false }); if (ctrl.stepReplay()) sync() }
      else if (e.key === 'ArrowLeft') { set({ playing: false }); if (ctrl.stepBackReplay()) sync() }
      else return
      // capture phase + stop: a focused control (e.g. the speed dropdown) must not also react to the arrow
      e.preventDefault()
      e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [mode, ctrl, set, sync])

  // leaving replay from any mode (e.g. the toolbar Replay button while picking) shows the latest data again
  useEffect(() => {
    if (mode === 'off' && ctrl.replayIndex !== null) ctrl.stopReplay()
  }, [mode, ctrl])

  // picking: Esc cancels; a cut line follows the mouse and shades the bars that will be hidden
  useEffect(() => {
    if (mode !== 'picking') return
    // Esc while picking from a running replay goes back to it, as on TradingView; otherwise it leaves replay
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && set({ mode: ctrl.replayIndex === null ? 'off' : 'active' })
    const ts = ctrl.chart.timeScale()
    const onMove = (p: MouseEventParams<Time>) => {
      if (!p.point || p.logical === undefined) return setCut(null)
      // only shown bars can be picked: past the replay cursor the line stays on the last one
      const x = ts.logicalToCoordinate(Math.min(Math.round(p.logical), ctrl.shownBars.length - 1) as never)
      if (x === null) return setCut(null)
      const half = ts.options().barSpacing / 2
      setCut({ x: x + half, width: ts.width(), height: ctrl.chart.paneSize(0).height })
    }
    // the cut line is the cursor here, as on TradingView: hide the crosshair lines and their axis labels
    const crosshair = (visible: boolean) => ctrl.chart.applyOptions({
      crosshair: { vertLine: { visible, labelVisible: visible }, horzLine: { visible, labelVisible: visible } },
    })
    crosshair(false)
    window.addEventListener('keydown', onKey)
    ctrl.chart.subscribeCrosshairMove(onMove)
    return () => {
      window.removeEventListener('keydown', onKey)
      ctrl.chart.unsubscribeCrosshairMove(onMove)
      crosshair(true)
      setCut(null)
    }
  }, [mode, ctrl, set])

  if (mode === 'picking') {
    return cut && cut.x < cut.width ? (
      <div className="replay-cut" style={{ left: cut.x, width: cut.width - cut.x, height: cut.height }} aria-hidden>
        <span className="replay-cut-line" />
        <span className="replay-cut-icon"><Icon name="replay" size={13} /></span>
      </div>
    ) : null
  }
  if (mode !== 'active') return null
  const idx = ctrl.replayIndex ?? 0
  const total = ctrl.bars.length
  const atEnd = idx >= total - 1 && !ctrl.hasNewer
  return (
    <div className="replay-bar" role="toolbar" aria-label="Replay controls">
      <span className="replay-tag">REPLAY</span>
      <button className="icon-btn" title="Step back one bar (Shift+←)" aria-label="Step back" disabled={ctrl.loading || idx <= 0} onClick={() => { set({ playing: false }); back() }}>
        <Icon name="step" className="flip-x" />
      </button>
      <button className="icon-btn play" title={`${playing ? 'Pause' : 'Play'} (Shift+↓)`} aria-label={playing ? 'Pause' : 'Play'} disabled={ctrl.loading || atEnd}
        onClick={() => set({ playing: !playing })}>
        <Icon name={playing ? 'pause' : 'play'} />
      </button>
      <button className="icon-btn" title="Step forward one bar (Shift+→)" aria-label="Step forward" disabled={ctrl.loading || atEnd} onClick={() => { set({ playing: false }); step() }}>
        <Icon name="step" />
      </button>
      <Select<number> className="replay-speed" value={speed} ariaLabel="Replay speed" width={96}
        options={SPEEDS.map((s) => ({ value: s, label: `${s} bar/s` }))} onChange={(v) => set({ speed: v })} />
      <button className={`btn ghost sm movement ${smooth ? 'on' : ''}`} aria-pressed={smooth}
        title={smooth ? 'Candles form tick by tick from lower-timeframe data, like TradingView. Click for whole bars'
          : 'Whole bars at a time. Click to let candles form tick by tick'}
        onClick={() => set({ smooth: !smooth })}>{smooth ? 'Live candles' : 'Whole bars'}</button>
      <span className="replay-sep" />
      <span className="replay-time">
        <span className="mono" title="Replay time; candles closing after this time stay hidden">{fmtDateTime(cutoffTime ?? cursorTime, tz)}</span>
        <span className="muted small">{atEnd ? 'end of data' : `bar ${idx + 1} of ${total}`}</span>
      </span>
      <span className="replay-sep" />
      <button className="btn ghost sm" title="Pick another start bar" onClick={() => {
        // like TradingView: the chart stays where it is and later bars stay hidden, so an earlier bar can be picked
        set({ mode: 'picking', playing: false })
      }}>Select bar</button>
      <button className="btn ghost sm" title="Leave replay and go back to the latest data" onClick={() => {
        ctrl.stopReplay()
        set({ mode: 'off', playing: false, cursorTime: null, cutoffTime: null })
      }}>Exit replay</button>
    </div>
  )
}
