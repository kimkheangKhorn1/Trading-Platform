import { useEffect } from 'react'
import { Icon } from '../components/common/Icon'
import { fmtDateTime } from '../lib/format'
import { useApp } from '../state/app'
import { useReplay } from '../state/replay'
import type { ChartController } from './controller'

const SPEEDS = [0.5, 1, 2, 5, 10, 20]

/**
 * Bar replay: the chart only receives candles up to the cursor and indicators are recomputed by the
 * backend with ``end_time = cursor`` - later candles are never sent to the engine.
 */
export function ReplayBar({ ctrl }: { ctrl: ChartController }) {
  const { mode, playing, speed, cursorTime, set } = useReplay()
  const tz = useApp((s) => s.timezone)

  useEffect(() => {
    if (mode !== 'active' || !playing) return
    const id = setInterval(() => {
      if (!ctrl.stepReplay()) set({ playing: false })
      else set({ cursorTime: ctrl.shownBars[ctrl.shownBars.length - 1][0] })
    }, 1000 / speed)
    return () => clearInterval(id)
  }, [mode, playing, speed, ctrl, set])

  useEffect(() => {
    if (mode !== 'picking') return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && set({ mode: 'off' })
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mode, set])

  if (mode !== 'active') return null
  const atEnd = ctrl.replayIndex !== null && ctrl.replayIndex >= ctrl.bars.length - 1
  const step = () => {
    if (ctrl.stepReplay()) set({ cursorTime: ctrl.shownBars[ctrl.shownBars.length - 1][0] })
  }
  const exit = () => {
    ctrl.stopReplay()
    set({ mode: 'off', playing: false, cursorTime: null })
  }
  return (
    <div className="replay-bar" role="toolbar" aria-label="Replay controls">
      <span className="replay-tag">REPLAY</span>
      <button className="icon-btn" title={playing ? 'Pause' : 'Play'} disabled={atEnd} onClick={() => set({ playing: !playing })}>
        <Icon name={playing ? 'pause' : 'play'} />
      </button>
      <button className="icon-btn" title="Step forward one bar" disabled={atEnd || playing} onClick={step}>
        <Icon name="step" />
      </button>
      <select className="input select sm" value={speed} aria-label="Replay speed" onChange={(e) => set({ speed: Number(e.target.value) })}>
        {SPEEDS.map((s) => (
          <option key={s} value={s}>{s} bar/s</option>
        ))}
      </select>
      <span className="mono muted">{fmtDateTime(cursorTime, tz)}</span>
      <button className="btn ghost sm" onClick={() => {
        ctrl.stopReplay() // show full history again so any bar can be picked
        set({ mode: 'picking', playing: false, cursorTime: null })
      }}>Select bar</button>
      <button className="btn ghost sm" onClick={exit}>Exit replay</button>
    </div>
  )
}
