import { useEffect, useState, type MouseEvent } from 'react'
import { fmtDateTime, TIMEZONES, type TimezoneId } from '../lib/format'
import { currentFeed, useApp } from '../state/app'
import { useScale, type PriceScaleKind } from '../state/scale'
import { Icon } from './common/Icon'
import { Select } from './common/Fields'

/** Mouse clicks should not leave a focus ring on the toggle (keyboard focus keeps it). */
const blurIfMouse = (e: MouseEvent<HTMLButtonElement>) => { if (e.detail > 0) e.currentTarget.blur() }

export function ChartFooter() {
  const tz = useApp((s) => s.timezone)
  const setTz = useApp((s) => s.setTimezone)
  useApp((s) => s.feeds)
  const feed = currentFeed()
  const scale = useScale()
  const toggleKind = (k: PriceScaleKind) => scale.set({ kind: scale.kind === k ? 'normal' : k })
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000))
  useEffect(() => {
    const id = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="chart-footer">
      <span className="muted">
        {feed ? `${feed.display_name} · ${feed.price_type} prices · ${feed.alignment === 'ny17' ? 'sessions 17:00 New York' : 'sessions 00:00 UTC'}` : ''}
      </span>
      <div className="grow" />
      <span className="mono clock">{fmtDateTime(now, tz).slice(11)}:{String(now % 60).padStart(2, '0')}</span>
      <Select<TimezoneId> className="compact" value={tz} onChange={setTz} ariaLabel="Display timezone" width={160}
        options={TIMEZONES.map((z) => ({ value: z.id, label: z.id === 'UTC' ? 'UTC' : z.label }))} />
      <div className="scale-btns" role="group" aria-label="Price scale">
        <button className={`scale-btn ${scale.kind === 'percent' ? 'on' : ''}`} aria-pressed={scale.kind === 'percent'}
          title="Percent scale: prices as % change from the first visible bar" onClick={(e) => { toggleKind('percent'); blurIfMouse(e) }}>%</button>
        <button className={`scale-btn ${scale.kind === 'log' ? 'on' : ''}`} aria-pressed={scale.kind === 'log'}
          title="Logarithmic scale" onClick={(e) => { toggleKind('log'); blurIfMouse(e) }}>log</button>
        <button className={`scale-btn ${scale.auto ? 'on' : ''}`} aria-pressed={scale.auto}
          title="Auto-scale: fit prices to the visible bars. Turns off when you drag the price axis" onClick={(e) => { scale.set({ auto: !scale.auto }); blurIfMouse(e) }}>auto</button>
        <button className="scale-btn icon" title="Reset chart (Alt+R)" aria-label="Reset chart" onClick={(e) => { scale.reset(); blurIfMouse(e) }}>
          <Icon name="replay" size={13} />
        </button>
      </div>
    </div>
  )
}
