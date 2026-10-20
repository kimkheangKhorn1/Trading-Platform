import { useEffect, useRef, useState } from 'react'
import { BADGE_TFS, OVERLAP_STYLE, REACTION_WINDOW } from '../api/badgeOverlap'
import type { BadgeOverlapSettings } from '../api/badgeOverlap'
import type { IndicatorInstance } from '../state/indicators'
import { TF_LABELS } from '../state/app'
import { overlapSettings, signalsOn } from './useBadgeOverlaps'
import { SIGNAL_BUFFER, SIGNAL_MAX_RISK, SIGNAL_RR } from './overlapSignals'

const COPY = {
  title: 'Demand / Supply overlaps',
  about: 'Where fresh demand or supply zones from several timeframes cover the same price.',
  how: [
    'Found from the fresh D/S price badges the chart displays, then locked: the range and timeframe count never change afterwards.',
    'The first touch of an overlap’s own band deletes it. Its source zones then cannot form a NEW overlap; other locked overlaps stay until their own band is touched.',
    '“Hide Zones Farther Than” only hides an overlap while price is far; it returns unchanged when price comes back.',
    'Locks survive reloads and timeframe switches. Replay shows only the locks known at its clock.',
  ],
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return <button type="button" role="switch" aria-checked={checked} className="ovl-toggle" onClick={() => onChange(!checked)}>
    <span className="ovl-toggle-text"><span>{label}</span>{hint && <small>{hint}</small>}</span>
    <span className="ovl-switch" aria-hidden="true"><span /></span>
  </button>
}

export function Chips<T extends string | number>({ label, options, selected, onToggle, render }: {
  label: string; options: readonly T[]; selected: (v: T) => boolean; onToggle: (v: T) => void; render: (v: T) => string
}) {
  return <div className="ovl-field">
    <span className="ovl-label">{label}</span>
    <div className="ovl-chips" role="group" aria-label={label}>
      {options.map(v => <button type="button" key={String(v)} aria-pressed={selected(v)} onClick={() => onToggle(v)}>{render(v)}</button>)}
    </div>
  </div>
}

/** Legend pill + settings popover for the D/S overlap layer of the SMC indicator. */
export function OverlapControl({ inst, update }: {
  inst: IndicatorInstance; update: (uid: string, patch: Partial<IndicatorInstance>) => void
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement>(null)
  const c = overlapSettings(inst)
  const st = OVERLAP_STYLE
  const copy = COPY
  const change = (patch: Partial<BadgeOverlapSettings>) => update(inst.uid, {badgeOverlap: {...c, ...patch}})
  useEffect(() => {
    if (!open) return
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', outside)
    window.addEventListener('keydown', esc)
    return () => { window.removeEventListener('mousedown', outside); window.removeEventListener('keydown', esc) }
  }, [open])
  const trade = signalsOn(c)
  const dot = {background: `linear-gradient(90deg, ${st.demand} 50%, ${st.supply} 50%)`}
  return <span className="badge-overlap-control" ref={root}>
    <button className="ovl-pill" aria-pressed={c.enabled}
      aria-label={c.enabled ? `Hide ${st.title}s` : `Show ${st.title}s`} title={copy.about}
      onClick={() => change({enabled: !c.enabled})}>
      <i style={dot} aria-hidden="true" />{st.title}
    </button>
    <button className="ovl-trade" aria-pressed={trade}
      aria-label={trade ? `Stop trading ${st.title}s` : `Trade ${st.title}s`}
      title={`${trade ? 'Trading' : 'Not trading'} ${st.title} reversal signals on 5m (click to switch). SL candle shadow + $${SIGNAL_BUFFER}, max $${SIGNAL_MAX_RISK} · TP 1R / 2R / 3R`}
      onClick={() => change({signals: !trade, ...(!trade && !c.enabled ? {enabled: true} : {})})}>
      {trade ? '● Trade' : '○ Trade'}
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
      </section>
      <section>
        <Toggle label="Only the highest count for new locks" hint="★ marks the highest count either way"
          checked={c.strongestOnly ?? false} onChange={strongestOnly => change({strongestOnly})} />
        <Toggle label="Candle reactions on 1m / 5m"
          hint={`Checks ${REACTION_WINDOW.M1} candles on 1m, ${REACTION_WINDOW.M5} on 5m, from the first touch`}
          checked={c.candleReactions ?? true} onChange={candleReactions => change({candleReactions})} />
        <Toggle label="Trade D/S overlaps (5m)"
          hint={`BUY / SELL on a reversal candle at a touched overlap · SL candle shadow + $${SIGNAL_BUFFER} (max $${SIGNAL_MAX_RISK}) · TP ${SIGNAL_RR.map(r => r + 'R').join(' / ')} · replay dashboard`}
          checked={trade} onChange={signals => change({signals})} />
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
          <li>Trade (5m chart, the ● Trade button or the switch above): the first confirmed reaction at a touched overlap is a rejection (bullish pattern at demand / bearish at supply, closing beyond the band) → BUY / SELL at its close. SL is ${SIGNAL_BUFFER} beyond the signal candle's shadow (below its low for a buy, above its high for a sell); when that would make the stop wider than ${SIGNAL_MAX_RISK}, the ${SIGNAL_BUFFER} is cut to keep it at ${SIGNAL_MAX_RISK}, and a shadow longer than ${SIGNAL_MAX_RISK} on its own means no trade. TP1 / TP2 / TP3 = 1R / 2R / 3R of that stop. The SL never moves: SL first = −1R, each TP reached = +1R (SL after TP1 = +1R, after TP2 = +2R, TP3 = +3R); when one candle touches both, its 1m candles decide. One trade at a time, like REAPER [JOAT]. The dashboard appears in replay and counts the trades signalled after the replay start bar. Chart candles, no spread.</li>
        </ul>
      </details>
    </div>}
  </span>
}
