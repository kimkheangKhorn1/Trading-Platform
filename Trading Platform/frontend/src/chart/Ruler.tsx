import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { Logical } from 'lightweight-charts'
import { TF_SECONDS, useApp } from '../state/app'
import { useReplay } from '../state/replay'
import { useRuler } from '../state/ruler'
import type { ChartController } from './controller'
import { timeToLogical } from './timeIndex'
import { rulerDuration, rulerMetrics, rulerTime, type RulerPoint } from './rulerMath'

interface Selection { start: RulerPoint; end: RulerPoint; complete: boolean }
interface View { width: number; height: number; x1: number; y1: number; x2: number; y2: number }
const typing = (target: EventTarget | null) => target instanceof HTMLElement
  && !!target.closest('input, textarea, select, [contenteditable=true], [role=dialog]')

/** Screen overlay, anchored in price/time so measurements stay correct with zoom and log/% scales. */
export function Ruler({ ctrl }: { ctrl: ChartController }) {
  const enabled = useRuler((s) => s.enabled)
  const feed = useApp((s) => s.feedId)
  const tf = useApp((s) => s.timeframe)
  const replayMode = useReplay((s) => s.mode)
  const [shortcut, setShortcut] = useState(false)
  const active = (enabled || shortcut) && replayMode !== 'picking'

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target)) return
      if (e.key === 'Shift' && useReplay.getState().mode !== 'picking') setShortcut(true)
      if (e.key === 'Escape') { useRuler.getState().setEnabled(false); setShortcut(false) }
    }
    const release = (e: KeyboardEvent) => { if (e.key === 'Shift') setShortcut(false) }
    const blur = () => setShortcut(false)
    window.addEventListener('keydown', onKey)
    window.addEventListener('keyup', release)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('keyup', release)
      window.removeEventListener('blur', blur)
    }
  }, [])

  return active ? <RulerSurface key={`${feed}:${tf}`} ctrl={ctrl} /> : null
}

function RulerSurface({ ctrl }: { ctrl: ChartController }) {
  const [selection, setSelection] = useState<Selection | null>(null)
  const current = useRef<Selection | null>(null)
  const down = useRef<{ x: number; y: number } | null>(null)
  const [view, setView] = useState<View | null>(null)
  const update = (s: Selection | null) => { current.current = s; setSelection(s) }

  useEffect(() => {
    const remove = (e: KeyboardEvent) => {
      if (!typing(e.target) && (e.key === 'Delete' || e.key === 'Backspace') && current.current) {
        e.preventDefault()
        current.current = null
        setSelection(null)
      }
    }
    window.addEventListener('keydown', remove)
    return () => window.removeEventListener('keydown', remove)
  }, [])

  useEffect(() => {
    let frame: number
    const paint = () => {
      const { width, height } = ctrl.chart.paneSize(0)
      const s = current.current
      const times = ctrl.times
      const coordinate = (p: RulerPoint) => ({
        x: ctrl.chart.timeScale().logicalToCoordinate(timeToLogical(times, p.time, TF_SECONDS[ctrl.tf]) as Logical),
        y: ctrl.candles.priceToCoordinate(p.price),
      })
      const a = s ? coordinate(s.start) : null
      const b = s ? coordinate(s.end) : null
      const next = { width, height, x1: a?.x ?? 0, y1: a?.y ?? 0, x2: b?.x ?? 0, y2: b?.y ?? 0 }
      setView((old) => old && Object.entries(next).every(([k, v]) => old[k as keyof View] === v) ? old : next)
      frame = requestAnimationFrame(paint)
    }
    frame = requestAnimationFrame(paint)
    return () => cancelAnimationFrame(frame)
  }, [ctrl])

  if (!view) return null
  const point = (e: ReactPointerEvent<SVGSVGElement>): RulerPoint | null => {
    if (ctrl.loading || !ctrl.shownBars.length) return null
    const rect = e.currentTarget.getBoundingClientRect()
    const x = Math.max(0, Math.min(view.width, e.clientX - rect.left))
    const y = Math.max(0, Math.min(view.height, e.clientY - rect.top))
    const logical = ctrl.chart.timeScale().coordinateToLogical(x)
    const price = ctrl.candles.coordinateToPrice(y)
    if (logical === null || price === null) return null
    const tick = 10 ** -ctrl.precision
    return { time: rulerTime(ctrl.times, logical, TF_SECONDS[ctrl.tf]), price: Math.round(price / tick) * tick }
  }
  const move = (e: ReactPointerEvent<SVGSVGElement>) => {
    const end = point(e)
    const s = current.current
    if (end && s && !s.complete) update({ ...s, end })
  }
  const begin = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return
    const p = point(e)
    if (!p) return
    e.preventDefault()
    if (current.current && !current.current.complete) {
      update({ ...current.current, end: p, complete: true })
      down.current = null
    } else {
      update({ start: p, end: p, complete: false })
      down.current = { x: e.clientX, y: e.clientY }
      e.currentTarget.setPointerCapture(e.pointerId)
    }
  }
  const finish = (e: ReactPointerEvent<SVGSVGElement>) => {
    const start = down.current
    if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 4) {
      const end = point(e)
      if (end && current.current) update({ ...current.current, end, complete: true })
    }
    down.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  const m = selection ? rulerMetrics(selection.start, selection.end, ctrl.times, TF_SECONDS[ctrl.tf], ctrl.precision) : null
  const rising = (m?.change ?? 0) >= 0
  const color = rising ? '#2962ff' : '#f23645'
  const left = Math.min(view.x1, view.x2)
  const top = Math.min(view.y1, view.y2)
  const width = Math.abs(view.x2 - view.x1)
  const height = Math.abs(view.y2 - view.y1)
  const sign = (v: number, digits: number) => `${v > 0 ? '+' : ''}${v.toFixed(digits)}`
  const labelX = Math.max(8, Math.min(view.width - 252, left + width / 2 - 122))
  const labelY = Math.max(8, Math.min(view.height - 94, top + height + 10))
  return <>
    <svg className="ruler-surface" width={view.width} height={view.height} aria-label="Ruler measuring surface"
      onPointerDown={begin} onPointerMove={move} onPointerUp={finish}
      onPointerCancel={() => { down.current = null; update(null) }}>
      {selection ? <g stroke={color} fill="none">
        <rect x={left} y={top} width={Math.max(1, width)} height={Math.max(1, height)} fill={color} fillOpacity={0.13} strokeOpacity={0.8} />
        <path d={`M${view.x1} ${view.y1}H${view.x2}V${view.y2}`} strokeDasharray="4 3" />
        <path d={`M${left} ${top + height / 2}H${left + width}M${left + width / 2} ${top}V${top + height}`} strokeWidth={1.5} />
        <circle cx={view.x1} cy={view.y1} r={3} fill={color} />
        <circle cx={view.x2} cy={view.y2} r={3} fill={color} />
      </g> : null}
    </svg>
    {m && selection ? <div className="ruler-result mono" role="status" aria-label="Ruler measurement"
      style={{ left: labelX, top: labelY, background: color }}>
      <strong>{sign(m.change, ctrl.precision)} ({m.percent === null ? '—' : `${sign(m.percent, 2)}%`})</strong>
      <span>{m.ticks > 0 ? '+' : ''}{m.ticks.toLocaleString()} ticks · {m.bars} bars · {rulerDuration(m.elapsed)}</span>
      <span className="ruler-prices">{selection.start.price.toFixed(ctrl.precision)} → {selection.end.price.toFixed(ctrl.precision)}</span>
    </div> : null}
    <div className="ruler-hint">{selection?.complete ? 'Drag to measure again · Esc to close' : 'Drag between two points, or click start and end · Esc to close'}</div>
  </>
}
