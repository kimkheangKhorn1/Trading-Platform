import { useState } from 'react'
import { BADGE_OVERLAP_DEFAULTS, BADGE_TFS } from '../api/badgeOverlap'
import type { BadgeOverlapSettings } from '../api/badgeOverlap'
import type { IndicatorInstance } from '../state/indicators'
import { Select } from '../components/common/Fields'
import { TF_LABELS } from '../state/app'

export function BadgeOverlapControl({ inst, update }: {
  inst: IndicatorInstance; update: (uid: string, patch: Partial<IndicatorInstance>) => void
}) {
  const [open, setOpen] = useState(false)
  const c = inst.badgeOverlap ?? BADGE_OVERLAP_DEFAULTS
  const change = (patch: Partial<BadgeOverlapSettings>) => update(inst.uid, {badgeOverlap: {
    ...c, ...patch,
  }})
  return <span className="badge-overlap-control">
    <button className="legend-signals" aria-pressed={c.enabled}
      aria-label={c.enabled ? 'Hide fresh badge overlaps' : 'Show fresh badge overlaps'}
      title="Optional overlap of currently displayed fresh Demand/Supply badges"
      onClick={() => change({enabled: !c.enabled})}>{c.enabled ? 'D/S overlap on' : 'D/S overlap off'}</button>
    <button className="icon-btn sm" aria-label="Fresh badge overlap settings" aria-expanded={open}
      onClick={() => setOpen(!open)}>⌄</button>
    {open && <div className="badge-overlap-settings" role="group" aria-label="Fresh badge overlap settings">
      <strong>Locked D/S overlaps</strong>
      <span>Finds candidates from displayed fresh D/S badges, then locks their range and timeframe count.</span>
      <span>After locking: first touch of the overlap deletes it. “Hide Zones Farther Than” uses the current chart’s recent range to hide/show it without deletion.</span>
      <div className="badge-overlap-tfs">{BADGE_TFS.map(tf => <label key={tf}>
        <input type="checkbox" checked={c.timeframes.includes(tf)} onChange={e => change({timeframes: e.target.checked
          ? BADGE_TFS.filter(t => t === tf || c.timeframes.includes(t)) : c.timeframes.filter(t => t !== tf)})} />{TF_LABELS[tf]}
      </label>)}</div>
      <label>Minimum timeframes<Select value={c.minTimeframes} ariaLabel="Overlap minimum timeframes" width={78}
        options={[2,3,4,5,6].map(n => ({value:n,label:String(n)}))}
        onChange={minTimeframes => change({minTimeframes})} /></label>
      <label><input type="checkbox" checked={c.strongestOnly ?? false}
        onChange={e => change({strongestOnly: e.target.checked})} />Only highest count for new locks</label>
      <span>★ highlights captured timeframe counts. Existing locks never shrink when a source badge disappears.</span>
      <span>Locks are saved across reloads and chart timeframe changes. Touches are tracked even while hidden. Replay only shows locks known by its clock. Counts are from locking, not live trade confidence.</span>
      <button onClick={() => setOpen(false)}>Close</button>
    </div>}
  </span>
}

