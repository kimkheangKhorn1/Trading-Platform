import { useLayoutEffect, useRef, useState } from 'react'
import type { HitInfo } from './overlay'
import { BADGE_TF_LABELS } from '../api/badgeOverlap'
import { fmtDateTime } from '../lib/format'
import { useApp } from '../state/app'

/** Measure the actual card, then keep it inside the chart on all four edges. */
export function ChartTooltip({ info, x, y, pinned, onClose }: {info: HitInfo; x: number; y: number; pinned?: boolean; onClose?: () => void}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({left: x + 14, top: y + 14})
  const tz = useApp(s => s.timezone)
  useLayoutEffect(() => {
    const el = ref.current, parent = el?.parentElement
    if (!el || !parent) return
    const left = x + 14 + el.offsetWidth <= parent.clientWidth - 8 ? x + 14 : x - el.offsetWidth - 14
    const top = y + 14 + el.offsetHeight <= parent.clientHeight - 8 ? y + 14 : y - el.offsetHeight - 14
    setPos({left: Math.max(8, left), top: Math.max(8, top)})
  }, [info, x, y, tz, pinned])
  const detail = info.overlap
  if (!detail) return <div ref={ref} role="tooltip" className="chart-tip" style={pos}>
    <div className="chart-tip-title">{info.title}</div>
    {info.body && <div className="chart-tip-body">{info.body}</div>}
  </div>
  const {band: b, precision, strongest, asOf} = detail
  const ob = detail.kind === 'ob'
  const locked = b.locked_at !== undefined
  const f = (v: number) => v.toFixed(precision)
  const name = ob ? `${b.side === 'demand' ? 'Bullish' : 'Bearish'} order block overlap` : `${b.side === 'demand' ? 'Demand' : 'Supply'} overlap`
  return <div ref={ref} role={pinned ? 'dialog' : 'tooltip'} aria-label={`${name} details`} className={`chart-tip overlap-tip ${b.side} ${ob ? 'ob' : ''} ${pinned ? 'pinned' : ''}`} style={pos}>
    {pinned && <button className="overlap-tip-close" aria-label="Close overlap details" onClick={onClose}>×</button>}
    <div className="overlap-tip-heading"><strong>{name}</strong><span>{locked ? 'Locked · untouched' : ob ? 'At snapshot' : 'Fresh at snapshot'}</span></div>
    <div className="overlap-tip-range">{f(b.bottom)} <span>–</span> {f(b.top)}</div>
    <div className="overlap-tip-count">{b.strength} different timeframes {b.locked_at !== undefined ? 'at locking' : 'share this entire band'}</div>
    <p>{b.timeframes.map(tf => BADGE_TF_LABELS[tf] ?? tf).join(' + ')} · width {f(b.top - b.bottom)} price units</p>
    {strongest && <p className="overlap-tip-peak">★ Highest timeframe count on this side · ties included</p>}
    <div className="overlap-tip-sources"><strong>{locked ? (ob ? 'Original order blocks at locking' : 'Original sources at locking') : ob ? 'Source order blocks' : 'Source Demand/Supply zones'}</strong>
      {b.timeframes.map(tf => <div className="overlap-tip-source" key={tf}>
        <b>{BADGE_TF_LABELS[tf] ?? tf}</b><span>{b.badges.filter(z => z.timeframe === tf).map((z, i) =>
          <span key={i}>{f(z.bottom)} – {f(z.top)}{ob && z.kind ? <em> · {z.kind === 'swing' ? 'Swing' : 'Internal'}</em> : null}
            <small>{ob ? 'Order block candle' : 'Base candle'}: {fmtDateTime(z.origin, tz)}</small></span>)}</span>
      </div>)}
    </div>
    {locked ? <p>{ob ? 'The first touch of this band deletes it for good, and its order blocks cannot form a new overlap. Other locked overlaps stay until their own band is touched. Being far from price only hides it.'
        : 'Touching this overlap’s own boundary deletes this group. Its sources are retired from NEW overlaps. Other already-locked overlaps sharing those sources remain unchanged until their own boundaries are touched. Distance hiding is temporary.'}</p>
      : <p>{ob ? 'These are the order blocks the chart currently displays on each timeframe.' : 'Fresh: the source indicator has not marked these zones touched at their proximal level.'}</p>}
    <p className="overlap-tip-note">{locked ? 'Captured confluence, not current fresh-source count or trade confidence. Touch tracking uses observed completed 1m candles, including while hidden.' : 'Counts timeframes, not independent systems or trade confidence. Forming source candles can change the overlap.'}</p>
    <footer>{locked && <>Locked {fmtDateTime(b.locked_at!, tz)}<br /></>}Snapshot {fmtDateTime(asOf, tz)} · {tz === 'local' ? 'Local' : tz}<br />{ob ? 'The order block candle precedes the structure break that confirmed it.' : 'Base candle time is not the zone confirmation time.'}<br />{pinned ? 'Esc or × to close' : 'Click the zone to keep these details open and scroll.'}</footer>
  </div>
}
