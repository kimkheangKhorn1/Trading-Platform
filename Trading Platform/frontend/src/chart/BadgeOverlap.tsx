import { useEffect, useRef, useState } from 'react'
import { BADGE_TFS, OB_TYPES, OVERLAP_STYLE, REACTION_WINDOW } from '../api/badgeOverlap'
import type { BadgeOverlapSettings, ObType, OverlapKind } from '../api/badgeOverlap'
import type { IndicatorInstance } from '../state/indicators'
import { TF_LABELS } from '../state/app'
import { overlapSettings } from './useBadgeOverlaps'

const COPY: Record<OverlapKind, {title: string; about: string; how: string[]}> = {
  ds: {
    title: 'Demand / Supply overlaps',
    about: 'Where fresh demand or supply zones from several timeframes cover the same price.',
    how: [
      'Found from the fresh D/S price badges the chart displays, then locked: the range and timeframe count never change afterwards.',
      'The first touch of an overlap’s own band deletes it. Its source zones then cannot form a NEW overlap; other locked overlaps stay until their own band is touched.',
      '“Hide Zones Farther Than” only hides an overlap while price is far; it returns unchanged when price comes back.',
      'Locks survive reloads and timeframe switches. Replay shows only the locks known at its clock.',
    ],
  },
  ob: {
    title: 'Overlapped order blocks',
    about: 'Where order blocks from several timeframes stack on the same price.',
    how: [
      'Found from the order blocks the chart displays on each source timeframe (bullish = blue, bearish = orange), then locked: the range and timeframe count never change afterwards.',
      'Untouched only: an order block that price has already returned to since it formed does not count.',
      'The first touch of an overlap’s own band deletes it, and its order blocks cannot form a new overlap. Other locked overlaps stay until their own band is touched.',
      '“Hide Zones Farther Than” only hides an overlap while price is far; it returns unchanged when price comes back.',
    ],
  },
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return <button type="button" role="switch" aria-checked={checked} className="ovl-toggle" onClick={() => onChange(!checked)}>
    <span className="ovl-toggle-text"><span>{label}</span>{hint && <small>{hint}</small>}</span>
    <span className="ovl-switch" aria-hidden="true"><span /></span>
  </button>
}

function Chips<T extends string | number>({ label, options, selected, onToggle, render }: {
  label: string; options: readonly T[]; selected: (v: T) => boolean; onToggle: (v: T) => void; render: (v: T) => string
}) {
  return <div className="ovl-field">
    <span className="ovl-label">{label}</span>
    <div className="ovl-chips" role="group" aria-label={label}>
      {options.map(v => <button type="button" key={String(v)} aria-pressed={selected(v)} onClick={() => onToggle(v)}>{render(v)}</button>)}
    </div>
  </div>
}

/** Legend pill + settings popover for one overlap layer (D/S or order blocks) of the SMC indicator. */
export function OverlapControl({ inst, update, kind }: {
  inst: IndicatorInstance; update: (uid: string, patch: Partial<IndicatorInstance>) => void; kind: OverlapKind
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement>(null)
  const c = overlapSettings(inst, kind)
  const st = OVERLAP_STYLE[kind]
  const copy = COPY[kind]
  const change = (patch: Partial<BadgeOverlapSettings>) =>
    update(inst.uid, {[kind === 'ob' ? 'obOverlap' : 'badgeOverlap']: {...c, ...patch}})
  useEffect(() => {
    if (!open) return
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', outside)
    window.addEventListener('keydown', esc)
    return () => { window.removeEventListener('mousedown', outside); window.removeEventListener('keydown', esc) }
  }, [open])
  const types = c.obTypes ?? OB_TYPES
  const dot = {background: `linear-gradient(90deg, ${st.demand} 50%, ${st.supply} 50%)`}
  return <span className="badge-overlap-control" ref={root}>
    <button className="ovl-pill" aria-pressed={c.enabled}
      aria-label={c.enabled ? `Hide ${st.title}s` : `Show ${st.title}s`} title={copy.about}
      onClick={() => change({enabled: !c.enabled})}>
      <i style={dot} aria-hidden="true" />{st.title}
    </button>
    <button className="icon-btn sm" aria-label={`${copy.title} settings`} aria-expanded={open}
      onClick={() => setOpen(!open)}>⌄</button>
    {open && <div className="ovl-panel" role="dialog" aria-label={`${copy.title} settings`}>
      <header>
        <i style={dot} aria-hidden="true" />
        <div><strong>{copy.title}</strong><span>{copy.about}</span></div>
        <button type="button" role="switch" aria-checked={c.enabled} aria-label={`${copy.title} on`}
          className="ovl-switch-only" onClick={() => change({enabled: !c.enabled})}><span className="ovl-switch"><span /></span></button>
        <button type="button" className="ovl-close" aria-label="Close" onClick={() => setOpen(false)}>×</button>
      </header>
      <section>
        <Chips label="Source timeframes" options={BADGE_TFS} render={tf => TF_LABELS[tf]}
          selected={tf => c.timeframes.includes(tf)}
          onToggle={tf => change({timeframes: c.timeframes.includes(tf) ? c.timeframes.filter(t => t !== tf)
            : BADGE_TFS.filter(t => t === tf || c.timeframes.includes(t))})} />
        <Chips label="Minimum timeframes overlapping" options={[2, 3, 4, 5, 6] as const} render={String}
          selected={n => c.minTimeframes === n} onToggle={n => change({minTimeframes: n})} />
        {kind === 'ob' && <Chips<ObType> label="Order block types" options={OB_TYPES}
          render={t => t === 'swing' ? 'Swing' : 'Internal'} selected={t => types.includes(t)}
          onToggle={t => {
            const next = types.includes(t) ? types.filter(x => x !== t) : OB_TYPES.filter(x => x === t || types.includes(x))
            if (next.length) change({obTypes: next})  // at least one type stays selected
          }} />}
      </section>
      <section>
        {kind === 'ob' && <Toggle label="Untouched order blocks only" hint="Skip blocks price already came back to"
          checked={c.freshOnly ?? true} onChange={freshOnly => change({freshOnly})} />}
        <Toggle label="Only the highest count for new locks" hint="★ marks the highest count either way"
          checked={c.strongestOnly ?? false} onChange={strongestOnly => change({strongestOnly})} />
        <Toggle label="Candle reactions on 1m / 5m"
          hint={`Checks ${REACTION_WINDOW.M1} candles on 1m, ${REACTION_WINDOW.M5} on 5m, from the first touch`}
          checked={c.candleReactions ?? true} onChange={candleReactions => change({candleReactions})} />
      </section>
      <div className="ovl-legend" aria-label="Symbols">
        <span><i style={{background: st.demand}} />{st.name.demand}</span>
        <span><i style={{background: st.supply}} />{st.name.supply}</span>
        <span>★ highest count</span>
        <span>↶ rejection · ➜ break · ? unconfirmed</span>
      </div>
      <details className="ovl-how">
        <summary>How it works</summary>
        <ul>
          {copy.how.map(t => <li key={t}>{t}</li>)}
          <li>Candle reactions use your enabled candlestick patterns on closed candles only (Long Lower Shadow excluded). Later visits do not restart the window, and the symbols stay after the box is gone.</li>
          <li>Counts are timeframes at locking: confluence, not a trade signal.</li>
        </ul>
      </details>
    </div>}
  </span>
}
