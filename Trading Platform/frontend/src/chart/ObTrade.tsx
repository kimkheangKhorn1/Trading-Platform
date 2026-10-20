import { useEffect, useRef, useState } from 'react'
import { OB_CONFIRMATIONS, OB_STYLE, OB_TFS } from '../api/obSignals'
import type { ObTradeSettings, ObType } from '../api/obSignals'
import type { IndicatorInstance } from '../state/indicators'
import { TF_LABELS } from '../state/app'
import { Chips, Toggle } from './BadgeOverlap'
import { SIGNAL_BUFFER, SIGNAL_MAX_RISK, SIGNAL_RR } from './overlapSignals'
import { obTradeSettings } from './useObSignals'

const TYPES: ObType[] = ['internal', 'swing']

/** Legend pill + settings popover for the order block trade signals of the SMC indicator (5m chart). */
export function ObTradeControl({ inst, update }: {
  inst: IndicatorInstance; update: (uid: string, patch: Partial<IndicatorInstance>) => void
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement>(null)
  const c = obTradeSettings(inst)
  const change = (patch: Partial<ObTradeSettings>) => update(inst.uid, {obTrade: {...c, ...patch}})
  useEffect(() => {
    if (!open) return
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', outside)
    window.addEventListener('keydown', esc)
    return () => { window.removeEventListener('mousedown', outside); window.removeEventListener('keydown', esc) }
  }, [open])
  const dot = {background: `linear-gradient(90deg, ${OB_STYLE.bull} 50%, ${OB_STYLE.bear} 50%)`}
  const n = c.confirmations
  const tfs = c.timeframes.map(t => TF_LABELS[t]).join(' · ') || 'no timeframe'
  const about = 'BUY / SELL on 5m when price comes back to an order block of your selected timeframes and confirms.'
  return <span className="badge-overlap-control" ref={root}>
    <button className="ovl-trade" aria-pressed={c.enabled}
      aria-label={c.enabled ? 'Stop trading order blocks' : 'Trade order blocks'}
      title={`${c.enabled ? 'Trading' : 'Not trading'} order blocks on 5m (click to switch): ${tfs} · ${n} confirmation candle${n > 1 ? 's' : ''} · SL shadow + $${SIGNAL_BUFFER}, max $${SIGNAL_MAX_RISK} · TP 1R / 2R / 3R`}
      onClick={() => change({enabled: !c.enabled})}>
      {c.enabled ? '● OB Trade' : '○ OB Trade'}
    </button>
    <button className="icon-btn sm" aria-label="Order block trade settings" aria-expanded={open}
      onClick={() => setOpen(!open)}>⌄</button>
    {open && <div className="ovl-panel" role="dialog" aria-label="Order block trade settings">
      <header>
        <i style={dot} aria-hidden="true" />
        <div><strong>Order block trade</strong><span>{about}</span></div>
        <button type="button" role="switch" aria-checked={c.enabled} aria-label="Order block trade on"
          className="ovl-switch-only" onClick={() => change({enabled: !c.enabled})}><span className="ovl-switch"><span /></span></button>
        <button type="button" className="ovl-close" aria-label="Close" onClick={() => setOpen(false)}>×</button>
      </header>
      <section>
        <Chips label="Order blocks from" options={OB_TFS} render={tf => TF_LABELS[tf]}
          selected={tf => c.timeframes.includes(tf)}
          onToggle={tf => change({timeframes: c.timeframes.includes(tf) ? c.timeframes.filter(t => t !== tf)
            : OB_TFS.filter(t => t === tf || c.timeframes.includes(t))})} />
        <Chips label="5m confirmation candles" options={OB_CONFIRMATIONS} render={String}
          selected={k => c.confirmations === k} onToggle={confirmations => change({confirmations})} />
        <Chips label="Order block type" options={TYPES} render={t => t === 'internal' ? 'Internal' : 'Swing'}
          selected={t => c.types.includes(t)}
          onToggle={t => change({types: c.types.includes(t) ? c.types.filter(x => x !== t) : TYPES.filter(x => x === t || c.types.includes(x))})} />
      </section>
      <section>
        <Toggle label="Show waiting order blocks" hint="The 2 nearest per timeframe and side, within the indicator's distance filter"
          checked={c.zones ?? true} onChange={zones => change({zones})} />
      </section>
      <details className="ovl-how">
        <summary>How it works</summary>
        <ul>
          <li>Order blocks come from the indicator's own order block logic run on each selected timeframe (your Order Block settings). Each block is traded on its own; they do not need to overlap.</li>
          <li>A block can be traded from the close of the candle that created it until a candle closes through it.</li>
          <li>Confirmation (5m chart): after a candle touches the block, {n === 1 ? 'one candle closes' : `${n} candles in a row close`} back beyond it in the reversal direction: bullish and above the top of a bullish block, bearish and below the bottom of a bearish block. The first one must touch the block or come right after a candle that did. A candle in between that does not confirm restarts the count.</li>
          <li>Each block gives one signal at most. A 5m close through the far side breaks the block.</li>
          <li>Trade, the same rule as the D/S overlaps: entry at the close of the last confirmation candle. SL ${SIGNAL_BUFFER} beyond the deepest shadow of the confirmation candles (below the low for a buy, above the high for a sell), cut to keep the stop at most ${SIGNAL_MAX_RISK}; a shadow longer than ${SIGNAL_MAX_RISK} on its own means no trade. TP1 / TP2 / TP3 = {SIGNAL_RR.map(r => r + 'R').join(' / ')}. The SL never moves: SL first = −1R, each TP reached = +1R; when one candle touches both, its 1m candles decide. One OB trade at a time (a D/S trade can be open at the same time).</li>
          <li>The dashboard appears in replay and counts the trades signalled after the replay start bar. Chart candles, no spread: a record, not a backtest.</li>
        </ul>
      </details>
    </div>}
  </span>
}
