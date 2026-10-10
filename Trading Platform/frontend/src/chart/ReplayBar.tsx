import { useEffect, useState } from 'react'
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
  const { mode, playing, speed, cursorTime, set } = useReplay()
  const tz = useApp((s) => s.timezone)
  const [cut, setCut] = useState<{ x: number; width: number; height: number } | null>(null)

  const sync = () => {
    const shown = ctrl.shownBars
    set({ cursorTime: shown.length ? shown[shown.length - 1][0] : null })
  }
  const step = () => { if (ctrl.stepReplay()) sync() }
  const back = () => { if (ctrl.stepBackReplay()) sync() }

  useEffect(() => {
    if (mode !== 'active' || !playing) return
    const id = setInterval(() => {
      if (!ctrl.stepReplay()) set({ playing: false })
      else set({ cursorTime: ctrl.shownBars[ctrl.shownBars.length - 1][0] })
    }, 1000 / speed)
    return () => clearInterval(id)
  }, [mode, playing, speed, ctrl, set])

  // keyboard shortcuts while replaying
  useEffect(() => {
    if (mode !== 'active') return
    const onKey = (e: KeyboardEvent) => {
      if (!e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || isTyping(e.target)) return
      const st = useReplay.getState()
      if (e.key === 'ArrowDown') set({ playing: !st.playing })
      else if (e.key === 'ArrowRight') { set({ playing: false }); if (ctrl.stepReplay()) set({ cursorTime: ctrl.shownBars[ctrl.shownBars.length - 1][0] }) }
      else if (e.key === 'ArrowLeft') { set({ playing: false }); if (ctrl.stepBackReplay()) set({ cursorTime: ctrl.shownBars[ctrl.shownBars.length - 1][0] }) }
      else return
      // capture phase + stop: a focused control (e.g. the speed dropdown) must not also react to the arrow
      e.preventDefault()
      e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [mode, ctrl, set])

  // picking: Esc cancels; a cut line follows the mouse and shades the bars that will be hidden
  useEffect(() => {
    if (mode !== 'picking') return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && set({ mode: 'off' })
    const ts = ctrl.chart.timeScale()
    const onMove = (p: MouseEventParams<Time>) => {
      if (!p.point || p.logical === undefined) return setCut(null)
      const x = ts.logicalToCoordinate(Math.round(p.logical) as never)
      if (x === null) return setCut(null)
      const half = ts.options().barSpacing / 2
      setCut({ x: x + half, width: ts.width(), height: ctrl.chart.paneSize(0).height })
    }
    window.addEventListener('keydown', onKey)
    ctrl.chart.subscribeCrosshairMove(onMove)
    return () => {
      window.removeEventListener('keydown', onKey)
      ctrl.chart.unsubscribeCrosshairMove(onMove)
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
  const atEnd = idx >= total - 1
  return (
    <div className="replay-bar" role="toolbar" aria-label="Replay controls">
      <span className="replay-tag">REPLAY</span>
      <button className="icon-btn" title="Step back one bar (Shift+←)" aria-label="Step back" disabled={idx <= 0} onClick={() => { set({ playing: false }); back() }}>
        <Icon name="step" className="flip-x" />
      </button>
      <button className="icon-btn play" title={`${playing ? 'Pause' : 'Play'} (Shift+↓)`} aria-label={playing ? 'Pause' : 'Play'} disabled={atEnd}
        onClick={() => set({ playing: !playing })}>
        <Icon name={playing ? 'pause' : 'play'} />
      </button>
      <button className="icon-btn" title="Step forward one bar (Shift+→)" aria-label="Step forward" disabled={atEnd} onClick={() => { set({ playing: false }); step() }}>
        <Icon name="step" />
      </button>
      <Select<number> className="replay-speed" value={speed} ariaLabel="Replay speed" width={96}
        options={SPEEDS.map((s) => ({ value: s, label: `${s} bar/s` }))} onChange={(v) => set({ speed: v })} />
      <span className="replay-sep" />
      <span className="replay-time">
        <span className="mono">{fmtDateTime(cursorTime, tz)}</span>
        <span className="muted small">{atEnd ? 'end of data' : `bar ${idx + 1} of ${total}`}</span>
      </span>
      <span className="replay-sep" />
      <button className="btn ghost sm" title="Pick another start bar" onClick={() => {
        ctrl.stopReplay() // show full history again so any bar can be picked
        set({ mode: 'picking', playing: false, cursorTime: null })
      }}>Select bar</button>
      <button className="btn ghost sm" title="Leave replay and go back to the latest data" onClick={() => {
        ctrl.stopReplay()
        set({ mode: 'off', playing: false, cursorTime: null })
      }}>Exit replay</button>
    </div>
  )
}
