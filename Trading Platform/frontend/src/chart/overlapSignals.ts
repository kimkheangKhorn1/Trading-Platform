import { OVERLAP_STYLE } from '../api/badgeOverlap'
import type { OverlapTrade } from '../api/badgeOverlap'
import type { DrawingObj, IndicatorTable, TableCell } from '../api/types'
import { fmtDateTime } from '../lib/format'
import type { TimezoneId } from '../lib/format'

/** REAPER [JOAT]'s colors and transparencies, so the overlap signals read like its RAID signals. */
const BUY = '#38BDF8'
const SELL = '#E11D48'
const WHITE = '#FFFFFF'
/** Pine color.new(c, transp) */
export function tr(hex6: string, transp: number): string {
  return hex6 + Math.round((100 - transp) * 2.55).toString(16).padStart(2, '0').toUpperCase()
}
export const SIGNAL_RR = [1, 2, 3]
/** SL = signal candle shadow end (low for a buy, high for a sell) moved SIGNAL_BUFFER further; the addition shrinks
 *  so the stop never exceeds SIGNAL_MAX_RISK, and a shadow longer than it alone = no trade (backend rule) */
export const SIGNAL_BUFFER = 3
export const SIGNAL_MAX_RISK = 15
const NA = '#00000000'
const TF_ORDER = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4']
const TF_TEXT: Record<string, string> = {M1: '1m', M5: '5m', M15: '15m', M30: '30m', H1: '1H', H4: '4H'}

/** What differs between the signal layers (D/S overlaps, order blocks): names, ids and texts. The trade rule is the same. */
export interface SignalKind {
  short: string
  group: string
  /** signal drawings (labels and trade plans) count down from here */
  idBase: number
  tableId: string
  title: string
  /** first lines of a signal's hover text */
  head: (t: OverlapTrade, f: (p: number) => string) => string
  /** label text after BUY / SELL, for all the signals of one candle */
  tag: (entries: OverlapTrade[]) => string
  /** dashboard header hover text: what is counted */
  about: string
  /** optional dashboard row splitting the record (label, key of a trade, key order) */
  split?: {label: string; key: (t: OverlapTrade) => string; order: string[]}
}

const obTf = (t: OverlapTrade) => String((t as OverlapTrade & {ob_timeframe?: string}).ob_timeframe ?? '')
export const DS_SIGNALS: SignalKind = {
  short: 'D/S', group: 'overlap_signals', idBase: -100000, tableId: 'overlap_signals', title: 'D/S OVERLAP SIGNALS',
  head: (t, f) => `D/S overlap ${t.side === 'long' ? 'BUY' : 'SELL'} · 5m reversal\n${t.name} at ${OVERLAP_STYLE.name[t.zone_side]} ${f(t.bottom)}–${f(t.top)}\nPatterns: ${t.patterns.join(', ')}`,
  tag: entries => patternText(entries[0]),
  about: `reversal candles (your enabled patterns) at touched ${OVERLAP_STYLE.long} locks on 5m`,
}
export const OB_SIGNALS: SignalKind = {
  short: 'OB', group: 'ob_signals', idBase: -200000, tableId: 'ob_signals', title: 'ORDER BLOCK SIGNALS',
  head: (t, f) => {
    const n = (t as OverlapTrade & {confirmations?: number}).confirmations ?? 1
    const buy = t.side === 'long'
    return `OB ${buy ? 'BUY' : 'SELL'} · ${n} × 5m confirmation\n${t.name}: ${f(t.bottom)}–${f(t.top)}\n${n} ${buy ? 'bullish' : 'bearish'} 5m candle${n > 1 ? 's' : ''} in a row closed ${buy ? 'above' : 'below'} the block after a touch`
  },
  tag: entries => [...new Set(entries.map(obTf))].sort((a, b) => TF_ORDER.indexOf(a) - TF_ORDER.indexOf(b))
    .map(tf => TF_TEXT[tf] ?? tf).join('+'),
  about: 'confirmation candles at the order blocks of your selected timeframes on 5m',
  split: {label: 'By OB TF', key: obTf, order: TF_ORDER},
}

/** signals that opened no trade */
const dim = (t: OverlapTrade) => t.status === 'skipped' || t.status === 'no_trade'
const patternText = (t: OverlapTrade) => t.code === 'BE' ? (t.side === 'long' ? 'Bull Eng' : 'Bear Eng') : t.code
const signed = (r: number) => `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(1)}R`

function tradeTip(t: OverlapTrade, precision: number, tz: TimezoneId, kind: SignalKind): string {
  const f = (p: number | null | undefined) => p === null || p === undefined ? '—' : p.toFixed(precision)
  const head = `${kind.head(t, p => f(p))}\nSignal candle closed: ${fmtDateTime(t.entry_time, tz)}`
  const shadow = Math.abs(t.entry - t.price)
  const stop = t.status === 'no_trade'
    ? `${kind === DS_SIGNALS ? 'Candle' : 'Confirmation'} shadow $${shadow.toFixed(2)}: longer than $${SIGNAL_MAX_RISK} on its own`
    : `SL ${f(t.initial_sl)} · stop $${t.risk.toFixed(2)} = candle shadow $${shadow.toFixed(2)} + $${(t.risk - shadow).toFixed(2)}${t.risk - shadow < SIGNAL_BUFFER - 1e-9 ? ` (cut from $${SIGNAL_BUFFER} to keep $${SIGNAL_MAX_RISK})` : ''}`
  if (t.status === 'skipped') return `${head}\nNo trade: the previous trade was still open (one trade at a time).`
  if (t.status === 'no_trade') return `${head}\n${stop}\nNo trade: the stop would be wider than $${SIGNAL_MAX_RISK}.`
  const tps = (t.tps ?? []).map((p, n) => `TP${n + 1} ${f(p)} (${SIGNAL_RR[n] ?? n + 1}R)`).join(' · ')
  const result = t.status === 'open'
    ? `Open · SL ${f(t.sl)}${t.tp_hit ? ` · TP${t.tp_hit} reached, +${t.tp_hit}R so far` : ''}`
    : `Closed ${fmtDateTime(t.exit_time, tz)} at ${f(t.exit_price)} · ${t.exit_reason} · ${signed(t.result_r ?? 0)}`
  return `${head}\nEntry ${f(t.entry)} (signal close) · ${stop}\n${tps}\n${result}\nThe SL never moves; each TP reached adds +1R (SL first = −1R, SL after TP1 / TP2 = +1R / +2R, TP3 = +3R). A candle touching SL and TP: its 1m candles decide. Chart candles, no spread.`
}

/** One setup = one signal candle and direction. Several locks touched by the same candle give the same trade
 *  (one opens, the rest are skipped): it is drawn and counted once. */
interface Setup { key: string; entries: OverlapTrade[]; rep: OverlapTrade; active: boolean }
const setupKey = (t: OverlapTrade) => `${t.time}:${t.side}`
const tradeKey = (t: OverlapTrade) => `${t.time}:${t.side}:${t.entry}`

function setups(trades: OverlapTrade[]): Setup[] {
  const by = new Map<string, OverlapTrade[]>()
  for (const t of trades) by.set(setupKey(t), [...(by.get(setupKey(t)) ?? []), t])
  return [...by.entries()].map(([key, entries]) => {
    const active = entries.filter(e => !dim(e))
    return {key, entries, rep: (active.length ? active : entries)[0], active: active.length > 0}
  }).sort((a, b) => a.rep.time - b.rep.time)
}

/** RAID-style BUY / SELL labels, one per signal candle and direction, and, like REAPER [JOAT], the
 *  entry / SL / TP plan of the newest trade.
 *  plans: 'none' = labels only (outside replay); {since: replay start bar} = also keep the plan of every trade
 *  signalled after it, to review them all until the replay is exited. */
export function overlapSignalDrawings(trades: OverlapTrade[], precision: number,
  tz: TimezoneId, lastTime: number | null, step: number, plans: 'newest' | 'none' | {since: number} = 'newest',
  kind: SignalKind = DS_SIGNALS): DrawingObj[] {
  let id = kind.idBase
  const out: DrawingObj[] = []
  const group = kind.group
  for (const s of setups(trades)) {
    const t = s.rep
    const buy = t.side === 'long'
    const col = buy ? BUY : SELL
    const tooFar = s.entries.every(e => e.status === 'no_trade') ? ` · SL>$${SIGNAL_MAX_RISK}` : ''
    out.push({kind: 'label', id: id--, group, created_at: t.entry_time, x: t.time,
      y: buy ? Math.min(...s.entries.map(e => e.price)) : Math.max(...s.entries.map(e => e.price)), yloc: 'price',
      style: buy ? 'label_up' : 'label_down', color: tr(col, s.active ? 8 : 55),
      text_color: s.active ? WHITE + 'FF' : tr(WHITE, 35), size: 'small',
      text: `${kind.short} ${buy ? 'BUY' : 'SELL'} · ${kind.tag(s.entries)}${tooFar}`,
      tooltip: tradeTip(s.entries[s.entries.length - 1], precision, tz, kind)})
  }
  const shown = new Map<string, OverlapTrade>()
  if (plans !== 'none') {
    const last = [...trades].reverse().find(t => !dim(t))
    if (last) shown.set(tradeKey(last), last)
    if (plans !== 'newest') for (const t of trades)
      if (t.time > plans.since && !dim(t) && !shown.has(tradeKey(t))) shown.set(tradeKey(t), t)
  }
  for (const last of shown.values()) {
    if (!last.tps || last.initial_sl === undefined) continue
    const d = last.side === 'long' ? 1 : -1
    const main = (d === 1 ? BUY : SELL) + 'FF'
    const buyLight = tr(BUY, 40), sellLight = tr(SELL, 40)
    // open: up to the next candle of the newest bar (JOAT extends to i + 1); closed: up to the candle that closed it
    const x1 = last.time
    const end = last.status === 'closed' && last.exit_time ? last.exit_time - step : (lastTime ?? last.time)
    const x2 = last.status === 'closed' ? end : end + step
    const tip = tradeTip(last, precision, tz, kind)
    const common = {group, created_at: last.entry_time, tooltip: tip}
    const line = (y: number, color: string, width: number, style: string): DrawingObj =>
      ({...common, kind: 'line', id: id--, x1, y1: y, x2, y2: y, color, width, style, extend: 'none'})
    const label = (y: number, text: string, color: string, text_color: string): DrawingObj =>
      ({...common, kind: 'label', id: id--, x: end, y, text, yloc: 'price', style: 'label_left', color, text_color, size: 'small'})
    const fill = (a: number, b: number, color: string): DrawingObj =>
      ({...common, kind: 'box', id: id--, left: x1, right: x2, top: Math.max(a, b), bottom: Math.min(a, b),
        border_color: NA, border_width: 0, border_style: 'solid', bg_color: color, extend: 'none',
        text: '', text_color: NA, text_size: 'small', text_halign: 'center', text_valign: 'center'})
    const f = (p: number) => p.toFixed(precision)
    const far = last.tps[last.tps.length - 1]
    out.push(
      fill(last.entry, last.initial_sl, tr(SELL, 76)),
      fill(last.entry, far, tr(BUY, 76)),
      line(last.entry, main, 2, 'solid'),
      label(last.entry, `${d === 1 ? 'BUY' : 'SELL'} ${f(last.entry)}`, main, d === 1 ? '#061620FF' : WHITE + 'FF'),
      line(last.initial_sl, sellLight, 1, 'dashed'),
      label(last.initial_sl, `SL ${f(last.initial_sl)}`, sellLight, WHITE + 'FF'),
      ...last.tps.flatMap((tp, n) => [
        line(tp, buyLight, 1, n === last.tps!.length - 1 ? 'dotted' : 'solid'),
        label(tp, `TP${n + 1} ${f(tp)}`, buyLight, WHITE + 'FF'),
      ]),
    )
  }
  return out
}

const isReaction = (d: DrawingObj) => d.kind === 'label' && d.group === OVERLAP_STYLE.group
  && d.id <= OVERLAP_STYLE.reactionId && d.id > OVERLAP_STYLE.reactionId - 10000

/** Overlap drawings without repeated candle-reaction symbols: none on a candle whose signal label (same direction)
 *  already names the pattern, and one per candle / direction / text. */
export function withoutRepeatedReactions(overlaps: DrawingObj[], signals: DrawingObj[]): DrawingObj[] {
  const signalled = new Set(signals.flatMap(d => d.kind === 'label' && d.style !== 'label_left' ? [`${d.x}:${d.style}`] : []))
  const seen = new Set<string>()
  return overlaps.filter(d => {
    if (d.kind !== 'label' || !isReaction(d)) return true
    const key = `${d.x}:${d.style}`
    if (signalled.has(key) || seen.has(`${key}:${d.text}`)) return false
    seen.add(`${key}:${d.text}`)
    return true
  })
}

function ago(seconds: number): string {
  const m = Math.max(0, Math.floor(seconds / 60))
  if (m < 60) return `${m}m ago`
  if (m < 1440) return `${Math.floor(m / 60)}h ${m % 60}m ago`
  return `${Math.floor(m / 1440)}d ${Math.floor(m % 1440 / 60)}h ago`
}

/** The replay dashboard of the overlap signals, styled like the Liquidity Reaper dashboard: the record of the trades
 *  signalled after the replay start bar. The position rows follow the chart (any open trade). */
export function overlapSignalTable(all: OverlapTrade[], ticker: string, tfLabel: string,
  precision: number, tz: TimezoneId, now: number | null, replayStart: number, kind: SignalKind = DS_SIGNALS): IndicatorTable | null {
  const recent = all.filter(t => t.time > replayStart)
  // Totals count each setup / trade once
  const signalSetups = setups(recent)
  const trades = signalSetups.map(s => s.rep)
  const unique = new Map<string, OverlapTrade>()
  for (const t of recent) if (!dim(t) && !unique.has(tradeKey(t))) unique.set(tradeKey(t), t)
  const traded = [...unique.values()]
  const closed = traded.filter(t => t.status === 'closed')
  const rs = closed.map(t => t.result_r ?? 0)
  const wins = rs.filter(r => r > 0), losses = rs.filter(r => r <= 0)
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0)
  const net = sum(rs)
  const usd = sum(closed.map(t => (t.result_r ?? 0) * t.risk))  // 0.01 lot = 1 oz: $1 per $1 move
  const size = 'normal'
  const rowBg = tr('#0E1830', 22), rowBgAlt = tr('#0A1326', 22)
  const lbl = tr(WHITE, 18), grey = tr(WHITE, 40)
  const purple = '#7A5AA8'
  const rows: TableCell[][] = []
  const cell = (text: string, text_color: string, bg_color: string, text_size = size, tooltip = ''): TableCell =>
    ({text, text_color, bg_color, text_size, tooltip})
  const row = (label: string, v1: string, c1: string, v2 = '', c2 = '#000000FF', bg2?: string, tip = '') => {
    const bg = rows.length % 2 === 0 ? rowBg : rowBgAlt
    rows.push([cell(label, lbl, bg, size, tip), cell(v1, c1, bg, size, tip), cell(v2, c2, bg2 ?? bg, size, tip)])
  }
  const rColor = (n: number) => n > 0 ? BUY + 'FF' : n < 0 ? SELL + 'FF' : grey
  rows.push([
    cell(`❄ ${kind.title}`, WHITE + 'FF', tr(BUY, 35), size,
      `Replay record: ${kind.about}, signalled after the replay start bar.\nEntry at the signal close · SL $${SIGNAL_BUFFER} beyond the candle shadow, the $${SIGNAL_BUFFER} cut so the stop stays at most $${SIGNAL_MAX_RISK}; a shadow longer than $${SIGNAL_MAX_RISK} = no trade · TP1 1R · TP2 2R · TP3 3R.\nOne trade at a time. The SL never moves; each TP reached adds +1R (SL first = −1R, SL after TP1 / TP2 = +1R / +2R, TP3 = +3R).\nChart candles, no spread: a record, not a backtest.`),
    cell('', NA, tr(purple, 40)),
    cell(`${ticker} · ${tfLabel}`, WHITE + 'FF', tr(SELL, 40)),
  ])
  rows.push([cell('', NA, tr(BUY, 60), 'tiny'), cell('', NA, tr(purple, 58), 'tiny'), cell('', NA, tr(SELL, 60), 'tiny')])
  row('Rules', `SL shadow + $${SIGNAL_BUFFER} · max $${SIGNAL_MAX_RISK}`, tr(SELL, 10), SIGNAL_RR.map(r => `${r}R`).join(' · ') + ' TP', tr(BUY, 10))
  const buys = trades.filter(t => t.side === 'long').length
  row('Signals', String(trades.length), tr(WHITE, 10), `${buys} buy · ${trades.length - buys} sell`, grey)
  row('Trades', `${closed.length} closed · ${traded.length - closed.length} open`, tr(WHITE, 10),
    `${signalSetups.filter(x => !x.active && x.entries.some(e => e.status === 'skipped')).length} skipped · ${signalSetups.filter(x => x.entries.every(e => e.status === 'no_trade')).length} SL > $${SIGNAL_MAX_RISK}`, grey,
    undefined, 'Skipped: a trade was already open. SL > $' + SIGNAL_MAX_RISK + ': the stop would have been too wide, so no trade.')
  row('Wins', String(wins.length), tr(BUY, 10), signed(sum(wins)), tr(BUY, 10))
  row('Losses', String(losses.length), tr(SELL, 10), signed(sum(losses)), tr(SELL, 10))
  row('Win Rate', closed.length ? `${Math.round(wins.length / closed.length * 100)}%` : '—', tr(WHITE, 10),
    closed.length ? `avg ${signed(net / closed.length)} / trade` : 'no closed trade yet', grey)
  row('Net R', signed(net), rColor(net),
    `${usd > 0 ? '+' : usd < 0 ? '−' : ''}$${Math.abs(usd).toFixed(0)} at 0.01 lot`, grey, undefined,
    '1R = that trade\'s stop in $. 0.01 lot of XAUUSD (1 oz) makes $1 per $1 move; before spread.')
  const reached = (n: number) => traded.filter(t => (t.tp_hit ?? 0) >= n).length
  row('TP Reached', SIGNAL_RR.map((_, n) => `TP${n + 1} ${reached(n + 1)}`).join(' · '), tr(BUY, 10),
    `SL ${closed.filter(t => (t.result_r ?? 0) < 0).length}`, tr(SELL, 10), undefined,
    'Trades that touched each target (a trade that reached TP2 also counts for TP1). SL = closed at the original stop (−1R).')
  if (kind.split) {
    const {key, order} = kind.split
    const keys = [...new Set(closed.map(key))].sort((a, b) => order.indexOf(a) - order.indexOf(b))
    row(kind.split.label, keys.length ? keys.map(k => {
      const mine = closed.filter(t => key(t) === k)
      return `${TF_TEXT[k] ?? k} ${signed(sum(mine.map(t => t.result_r ?? 0)))} (${mine.length})`
    }).join(' · ') : '—', tr(WHITE, 10), '', grey, undefined, 'Net R and closed trades per source timeframe.')
  }
  const newest = signalSetups.at(-1)
  row('Last Signal', newest ? `${kind.short} ${newest.rep.side === 'long' ? 'BUY' : 'SELL'}` : '—',
    newest ? (newest.rep.side === 'long' ? BUY : SELL) + 'FF' : grey,
    newest && now !== null ? ago(now - newest.rep.entry_time) : '', grey)
  const openBy = new Map<string, OverlapTrade>()
  for (const t of all) if (t.status === 'open' && !openBy.has(tradeKey(t))) openBy.set(tradeKey(t), t)
  const open = [...openBy.values()]
  const f = (p: number | undefined) => p === undefined ? '—' : p.toFixed(precision)
  if (!open.length) rows.push([cell('Position', lbl, rowBg), cell('○ FLAT', WHITE + 'FF', rowBg), cell('', NA, rowBg)])
  for (const t of open) {
    const d = t.side === 'long' ? 1 : -1
    const won = t.tp_hit ?? 0  // TPs reached so far: +1R each (the SL stays in place)
    rows.push([
      cell('Position', lbl, rowBg),
      cell(d === 1 ? '● LONG ACTIVE' : '● SHORT ACTIVE', WHITE + 'FF', tr(d === 1 ? BUY : SELL, 40)),
      cell(`SL ${f(t.sl)}${won > 0 ? ` · TP${won} ${signed(won)}` : ''}`, won > 0 ? tr(BUY, 10) : tr(SELL, 25), rowBg),
    ])
  }
  row('Replay', `since ${fmtDateTime(replayStart, tz)}`, grey, '', grey, undefined,
    'Counts the signals whose candle came after the replay start bar.')
  return {id: kind.tableId, position: 'top_right', rows, bg_color: tr('#0B1220', 8),
    border_width: 1, border_color: tr('#000000', 100), frame_width: 2, frame_color: tr(BUY, 35)}
}
