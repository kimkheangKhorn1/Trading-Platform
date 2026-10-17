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
      <strong>Fresh badge overlaps</strong>
      <span>Uses current red/green D/S price badges only.</span>
      <span>Follows “Hide Zones Farther Than” and the indicator’s D/S display settings on each source timeframe.</span>
      <div className="badge-overlap-tfs">{BADGE_TFS.map(tf => <label key={tf}>
        <input type="checkbox" checked={c.timeframes.includes(tf)} onChange={e => change({timeframes: e.target.checked
          ? BADGE_TFS.filter(t => t === tf || c.timeframes.includes(t)) : c.timeframes.filter(t => t !== tf)})} />{TF_LABELS[tf]}
      </label>)}</div>
      <label>Minimum timeframes<Select value={c.minTimeframes} ariaLabel="Overlap minimum timeframes" width={78}
        options={[2,3,4,5,6].map(n => ({value:n,label:String(n)}))}
        onChange={minTimeframes => change({minTimeframes})} /></label>
      <label><input type="checkbox" checked={c.strongestOnly ?? false}
        onChange={e => change({strongestOnly: e.target.checked})} />Only highest count per side</label>
      <span>★ marks the highest timeframe count for Demand and Supply separately. Ties stay visible.</span>
      <span>Only the exact shared price band is shaded. Each timeframe counts once, even with several badges. More timeframes does not mean a confirmed trade.</span>
      <button onClick={() => setOpen(false)}>Close</button>
    </div>}
  </span>
}

