import { useState, type CSSProperties } from 'react'
import type { ConfluencePanelData, IndicatorPanel } from '../api/types'
import { Icon, Spinner } from '../components/common/Icon'
import { fmtPrice } from '../lib/format'
import { load, save } from '../lib/storage'
import { useApp } from '../state/app'
import { useTuning } from '../state/tuning'
import type { ChartController } from './controller'

const fmtR = (v: number, d = 0) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(Number.isInteger(v) ? 0 : d || 2)}R`
const SHORT: Record<string, string> = { cardwell: 'Cardwell', arsi: 'Adaptive', structure: 'Structure' }
const ago = (n: number) => (n === 0 ? 'this bar' : `${n} bar${n > 1 ? 's' : ''} ago`)

/** Purpose-built dashboards of the visible indicators, pinned like Pine tables (only Triple Confluence so far). */
export function IndicatorPanels({ ctrl, panels }: { ctrl: ChartController; panels: { uid: string; panel: IndicatorPanel }[]; version: number }) {
  if (!panels.length) return null
  const right = ctrl.chart.priceScale('right').width() + 6
  const bottom = ctrl.chart.timeScale().height() + 6
  return (
    <>
      {panels.map(({ uid, panel }) => {
        const [v, h] = panel.position.split('_')
        const style: CSSProperties = {}
        if (h === 'left') style.left = 10
        else style.right = right
        if (v === 'bottom') style.bottom = bottom
        else style.top = 54
        return <ConfluencePanel key={uid} uid={uid} d={panel} style={style} />
      })}
    </>
  )
}

function ConfluencePanel({ uid, d, style }: { uid: string; d: ConfluencePanelData; style: CSSProperties }) {
  const openDialog = useApp((s) => s.openDialog)
  const tuningJob = useTuning((s) => (s.uid === uid ? s.job : null))
  const [open, setOpen] = useState(() => load<{ open: boolean }>('tp.confluencePanel', { open: true }).open)
  const toggle = () => {
    setOpen(!open)
    save('tp.confluencePanel', { open: !open })
  }
  const w = d.window
  const votes = d.engines.map((e) => {
    const bull = e.bull !== null && e.bull <= w
    const bear = e.bear !== null && e.bear <= w
    // the most recent vote counts when both are inside the window
    const side = bull && (!bear || e.bull! <= e.bear!) ? 'bull' : bear ? 'bear' : null
    return { ...e, side, age: side === 'bull' ? e.bull : side === 'bear' ? e.bear : null }
  })
  const nBull = votes.filter((v) => v.side === 'bull').length
  const nBear = votes.filter((v) => v.side === 'bear').length
  const running = tuningJob?.status === 'running'
  const s = d.stats

  return (
    <div className={`cf-panel ${open ? '' : 'closed'}`} style={style}>
      <header className="cf-head">
        <button className="cf-title" onClick={toggle} aria-expanded={open} title={open ? 'Collapse' : 'Expand'}>
          <Icon name={open ? 'chevronDown' : 'chevronRight'} size={12} />
          Triple Confluence
        </button>
        <span className={`cf-state ${d.trade ? (d.trade.long ? 'bull' : 'bear') : ''}`}
          title={d.trade ? 'A trade is running. New signals are ignored until it hits TP3 or the stop.' : 'No trade running - waiting for a signal.'}>
          {d.trade ? (d.trade.long ? '▲ In long' : '▼ In short') : 'No trade'}
        </span>
        <button className={`cf-tune ${running ? 'busy' : ''}`} onClick={() => openDialog({ type: 'tuning', uid })}
          title="Tune: search for the settings with the best results over a period">
          {running ? <><Spinner size={10} /> {tuningJob!.done}/{tuningJob!.budget}</> : <><Icon name="flask" size={12} /> Tune</>}
        </button>
      </header>
      {open ? (
        <>
          <section className="cf-sec">
            <div className="cf-label" title={`A BUY needs all 3 engines voting up within ${w} bars; a SELL all 3 voting down.${d.trade ? ' While a trade is running, a full 3/3 is ignored.' : ''}`}>
              Next signal
              <span className="cf-meter">
                <span className={`bull ${nBull === 3 ? 'full' : ''}`}>BUY {nBull}/3</span>
                <span className={`bear ${nBear === 3 ? 'full' : ''}`}>SELL {nBear}/3</span>
              </span>
            </div>
            <div className="cf-engines">
              {votes.map((e, k) => (
                <div key={e.key} className={`cf-engine ${e.side ?? ''}`} title={`${e.name}\n${e.tip}`}>
                  <span className="cf-engine-n">E{k + 1}</span>
                  <span className="cf-engine-name">{SHORT[e.key] ?? e.name}</span>
                  <span className="cf-engine-vote">
                    {e.side ? `${e.side === 'bull' ? '▲' : '▼'} ${ago(e.age!)}` : 'no vote yet'}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="cf-sec cf-market">
            <Gauge label="RSI" value={d.market.rsi} max={100} marks={[50]} tip={d.tips.RSI}
              tone={(v) => (v >= 50 ? 'bull' : 'bear')} />
            <Gauge label="ADX" value={d.market.adx} max={50} marks={[d.market.adx_min]} tip={d.tips.ADX}
              tone={(v) => (v >= d.market.adx_min ? 'bull' : 'flat')} />
            <div className="cf-htf" title={d.tips['HTF Bias']}>
              HTF <b className={d.market.htf ?? 'off'}>{d.market.htf === 'bull' ? '▲ Bull' : d.market.htf === 'bear' ? '▼ Bear' : 'Off'}</b>
            </div>
          </section>

          {d.trade ? <TradeCard d={d} /> : null}

          <section className="cf-sec">
            <div className="cf-label" title={`${d.tips.Trade}\nScore: ${d.score.rule}.`}>
              Results · {s.trades} trade{s.trades === 1 ? '' : 's'}
              <span className="muted">{s.since !== null ? 'since replay start' : 'on the loaded chart'}</span>
            </div>
            <div className="cf-stats">
              <Stat label="Wins" value={String(s.wins)} cls="bull" tip="Trades that reached TP3" />
              <Stat label="Losses" value={String(s.losses)} cls="bear" tip="Trades that hit the stop (some after TP1 / TP2)" />
              <Stat label="Win rate" value={s.trades ? `${Math.round(s.win_rate * 100)}%` : '—'} />
              <Stat label="Score" value={fmtR(s.rule)} cls={s.rule >= 0 ? 'bull' : 'bear'} big
                tip={`Your TP / SL rule: ${d.score.rule}. Real P&L below is what the money would actually be.`} />
            </div>
            <div className="cf-sub">
              <span title="Trades that reached each target">TP hits <b>{s.tp1}</b> · <b>{s.tp2}</b> · <b>{s.tp3}</b></span>
              <span title={`Real P&L, ${d.score.hold}. The TP / SL rule counts each TP as +1R even when the trade then stops out.`}>
                Real P&L <b className={s.hold >= 0 ? 'bull' : 'bear'}>{fmtR(s.hold, 1)}</b>
              </span>
            </div>
            {s.recent.length ? (
              <div className="cf-recent" title="Last finished trades, newest first: green = TP3, amber = stop after a TP, red = stop">
                <span className="cf-recent-label">Last {s.recent.length}</span>
                {s.recent.map((t, k) => (
                  <span key={k} className={`cf-dot ${t.outcome === 'tp3' ? 'win' : t.r >= 0 ? 'even' : 'loss'}`}
                    title={`${t.long ? 'Long' : 'Short'} · ${t.outcome === 'tp3' ? 'TP3' : `SL after ${t.hits} TP`} · ${fmtR(t.r)}`} />
                ))}
              </div>
            ) : null}
            {s.skipped ? <div className="cf-foot" title="Only one trade at a time: signals that came while a trade was running were not taken.">{s.skipped} signal{s.skipped > 1 ? 's' : ''} ignored because a trade was already open</div> : null}
          </section>
        </>
      ) : null}
    </div>
  )
}

function Gauge({ label, value, max, marks, tip, tone }: {
  label: string; value: number | null; max: number; marks: number[]; tip: string; tone: (v: number) => string
}) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="cf-gauge" title={tip}>
      <span className="cf-gauge-label">{label}</span>
      <span className="cf-gauge-track">
        <span className={`cf-gauge-fill ${value === null ? '' : tone(value)}`} style={{ width: `${pct}%` }} />
        {marks.map((m) => <span key={m} className="cf-gauge-mark" style={{ left: `${(m / max) * 100}%` }} />)}
      </span>
      <b>{value === null ? '—' : value.toFixed(0)}</b>
    </div>
  )
}

function Stat({ label, value, cls, tip, big }: { label: string; value: string; cls?: string; tip?: string; big?: boolean }) {
  return <div className={`cf-stat ${big ? 'big' : ''}`} title={tip}><span>{label}</span><b className={cls}>{value}</b></div>
}

/** The running trade as a ladder from the stop to TP3, with the live price position. */
function TradeCard({ d }: { d: ConfluencePanelData }) {
  const t = d.trade!
  const p = d.precision
  const levels = [
    { k: 'SL', v: t.sl, cls: 'sl', hit: false },
    { k: 'Entry', v: t.entry, cls: 'entry', hit: false },
    ...t.tps.map((v, j) => ({ k: `TP${j + 1}`, v, cls: 'tp', hit: t.hits[j] })),
  ]
  // live position on the ladder: -1R (stop) … +3R (TP3)
  const pos = t.live_r === null ? null : Math.max(0, Math.min(100, ((t.live_r + 1) / 4) * 100))
  const banked = t.hits.filter(Boolean).length
  return (
    <section className={`cf-sec cf-trade ${t.long ? 'bull' : 'bear'}`}>
      <div className="cf-label">
        {t.long ? '▲ Long' : '▼ Short'} trade · {t.bars} bar{t.bars === 1 ? '' : 's'}
        <span className={`cf-live ${t.live_r !== null && t.live_r >= 0 ? 'bull' : 'bear'}`}
          title="Where price is now, in R from the entry (1R = entry-to-stop distance)">
          {t.live_r === null ? '—' : fmtR(t.live_r, 2)}
        </span>
      </div>
      <div className="cf-ladder">
        <div className="cf-ladder-track">
          {pos !== null ? <span className="cf-ladder-now" style={{ left: `${pos}%` }} title="Price now" /> : null}
        </div>
        <div className="cf-ladder-steps">
          {levels.map((l) => (
            <div key={l.k} className={`cf-step ${l.cls} ${l.hit ? 'hit' : ''}`} title={`${l.k} ${fmtPrice(l.v, p)}`}>
              <span>{l.hit ? '✓ ' : ''}{l.k}</span>
              <b>{fmtPrice(l.v, p)}</b>
            </div>
          ))}
        </div>
      </div>
      <div className="cf-foot">
        {banked ? `${banked} of 3 targets reached (+${banked}R). ` : 'No target reached yet. '}
        If the stop is hit now: {fmtR(banked - 1)}
      </div>
    </section>
  )
}
