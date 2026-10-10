import { useEffect, useState } from 'react'
import { fmtDateTime, TIMEZONES, type TimezoneId } from '../lib/format'
import { currentFeed, useApp } from '../state/app'
import { Select } from './common/Fields'

export function ChartFooter() {
  const tz = useApp((s) => s.timezone)
  const setTz = useApp((s) => s.setTimezone)
  useApp((s) => s.feeds)
  const feed = currentFeed()
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
    </div>
  )
}
