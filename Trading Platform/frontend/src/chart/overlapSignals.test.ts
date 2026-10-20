import { describe, expect, it } from 'vitest'
import type { OverlapTrade } from '../api/badgeOverlap'
import { OB_SIGNALS, overlapSignalDrawings, overlapSignalTable, tr, withoutRepeatedReactions } from './overlapSignals'
import { obZoneDrawings } from '../api/obSignals'
import type { ObTrade, ObZone } from '../api/obSignals'

const S = 300
const base = (over: Partial<OverlapTrade>): OverlapTrade => ({
  id: 'a', lock_id: 'a', side: 'long', time: 0, entry_time: S, entry: 2000, risk: 10, price: 1995,
  zone_side: 'demand', bottom: 1990, top: 1996, code: 'H', name: 'Hammer', patterns: ['Hammer'],
  status: 'closed', initial_sl: 1990, sl: 2010, tps: [2010, 2020, 2030], tp_hit: 1,
  exit_time: 4 * S, exit_price: 2010, result_r: 1, exit_reason: 'TP1 lock', ...over,
})

describe('overlap signals', () => {
  it('matches Pine color.new transparency', () => {
    expect(tr('#38BDF8', 8)).toBe('#38BDF8EB')
    expect(tr('#000000', 100)).toBe('#00000000')
  })

  it('labels every signal and draws the plan of the newest trade only', () => {
    const trades = [base({}), base({id: 'b', time: 10 * S, entry_time: 11 * S, status: 'skipped'}),
      base({id: 'c', time: 20 * S, entry_time: 21 * S, side: 'short', status: 'open', entry: 2050, initial_sl: 2060,
        sl: 2060, tps: [2040, 2030, 2020], tp_hit: 0, exit_time: null, result_r: null})]
    const d = overlapSignalDrawings(trades, 2, 'UTC', 30 * S, S)
    const labels = d.filter(x => x.kind === 'label').map(x => x.kind === 'label' ? x.text : '')
    expect(labels.slice(0, 3)).toEqual(['D/S BUY · H', 'D/S BUY · H', 'D/S SELL · H'])
    expect(labels).toContain('SELL 2050.00')
    expect(labels).toContain('TP3 2020.00')
    const lines = d.filter(x => x.kind === 'line')
    expect(lines).toHaveLength(5)
    expect(lines.every(l => l.kind === 'line' && l.x1 === 20 * S && l.x2 === 31 * S)).toBe(true)
    expect(new Set(d.map(x => x.id)).size).toBe(d.length)
  })

  it('in replay keeps the plan of every trade since the start bar, each ending at its own exit', () => {
    const trades = [base({id: 'old', time: -10 * S, entry_time: -9 * S}), base({}),
      base({id: 'b', time: 10 * S, entry_time: 11 * S, status: 'skipped'}),
      base({id: 'c', time: 20 * S, entry_time: 21 * S, exit_time: 25 * S, entry: 2050})]
    const draw = (plans: 'newest' | 'none' | {since: number}) => overlapSignalDrawings(trades, 2, 'UTC', 30 * S, S, plans)
    const entries = (plans: 'newest' | 'none' | {since: number}) =>
      draw(plans).filter(x => x.kind === 'line' && x.style === 'solid' && x.width === 2)
    expect(entries('newest')).toHaveLength(1)
    const kept = entries({since: -1})
    expect(kept.map(l => l.kind === 'line' ? [l.x1, l.x2, l.y1] : [])).toEqual([[20 * S, 24 * S, 2050], [0, 3 * S, 2000]])
    const all = draw({since: -1})
    expect(new Set(all.map(x => x.id)).size).toBe(all.length)
    // outside replay: the signal labels only, no plan
    expect(draw('none').every(x => x.kind === 'label' && x.style !== 'label_left')).toBe(true)
    expect(draw('none')).toHaveLength(4)
  })

  it('counts wins, losses and R', () => {
    const trades = [base({}), base({id: 'b', time: 2 * S, result_r: -1, tp_hit: 0}),
      base({id: 'c', time: 4 * S, result_r: 3, tp_hit: 3})]
    const t = overlapSignalTable(trades, 'XAUUSD', '5m', 2, 'UTC', 10 * S, -1)!
    const byLabel = Object.fromEntries(t.rows.map(r => [r[0].text, r.slice(1).map(c => c.text)]))
    expect(byLabel['Wins']).toEqual(['2', '+4.0R'])
    expect(byLabel['Losses']).toEqual(['1', '−1.0R'])
    expect(byLabel['Net R'][0]).toBe('+3.0R')
    expect(byLabel['Win Rate'][0]).toBe('67%')
    expect(byLabel['TP Reached'][0]).toBe('TP1 2 · TP2 1 · TP3 1')
    expect(byLabel['Position']).toEqual(['○ FLAT', ''])
    expect(t.rows[0][0].text).toBe('❄ D/S OVERLAP SIGNALS')
    expect(t.rows.some(r => /onfluence|OB trades/.test(r[0].text))).toBe(false)
  })

  it('counts only signals after the replay start and shows stops that were too wide', () => {
    const trades = [base({id: 'old', time: 0, result_r: 3, tp_hit: 3}),
      base({id: 'a', time: 5 * S, entry_time: 6 * S, result_r: 2, risk: 8, tp_hit: 2}),
      base({id: 'b', time: 7 * S, entry_time: 8 * S, status: 'no_trade', risk: 16, tps: undefined})]
    const t = overlapSignalTable(trades, 'XAUUSD', '5m', 2, 'UTC', 10 * S, 0)!
    const byLabel = Object.fromEntries(t.rows.map(r => [r[0].text, r.slice(1).map(c => c.text)]))
    expect(byLabel['Signals'][0]).toBe('2')
    expect(byLabel['Trades']).toEqual(['1 closed · 0 open', '0 skipped · 1 SL > $15'])
    expect(byLabel['Net R']).toEqual(['+2.0R', '+$16 at 0.01 lot'])
    const d = overlapSignalDrawings(trades, 2, 'UTC', 10 * S, S)
    expect(d[2].kind === 'label' && d[2].text).toBe('D/S BUY · H · SL>$15')
  })

  it('draws one label and one plan when several locks signal on the same candle', () => {
    const trades = [base({id: 'd1', status: 'open', exit_time: null, result_r: null}),
      base({id: 'd2', status: 'skipped', tps: undefined})]
    const d = overlapSignalDrawings(trades, 2, 'UTC', 10 * S, S)
    const labels = d.filter(x => x.kind === 'label').map(x => x.kind === 'label' ? x.text : '')
    expect(labels.filter(x => x.includes('·'))).toEqual(['D/S BUY · H'])
    expect(labels.filter(x => x.startsWith('TP3'))).toHaveLength(1)
    const t = overlapSignalTable(trades, 'XAUUSD', '5m', 2, 'UTC', 10 * S, -1)!
    const byLabel = Object.fromEntries(t.rows.map(r => [r[0].text, r.slice(1).map(c => c.text)]))
    expect(byLabel['Signals'][0]).toBe('1')
    expect(byLabel['Trades']).toEqual(['0 closed · 1 open', '0 skipped · 0 SL > $15'])
    expect(byLabel['Position'][0]).toBe('● LONG ACTIVE')
  })

  it('drops reaction symbols a signal label repeats, and duplicate symbols', () => {
    const reaction = (id: number, group: string, x: number, text: string) => ({kind: 'label' as const, id, group, x, y: 1,
      text, yloc: 'belowbar' as const, style: 'label_up', color: '', text_color: '', size: 'tiny', tooltip: '', created_at: 0})
    const overlaps = [reaction(-30000, 'badge_overlap', 0, '↶ Bull Eng'), reaction(-30001, 'badge_overlap', S, '↶ H'),
      reaction(-30002, 'badge_overlap', S, '↶ H'), reaction(-30003, 'badge_overlap', S, '↶ P')]
    const signals = overlapSignalDrawings([base({})], 2, 'UTC', 10 * S, S)
    expect(withoutRepeatedReactions(overlaps, signals).map(x => x.id)).toEqual([-30001, -30003])
  })

  it('order block signals: own label, ids, dashboard and a per-timeframe split', () => {
    const ob = (over: Partial<ObTrade>): ObTrade => ({...base({code: 'OB', name: 'Bullish internal OB 1H',
      patterns: ['2 × 5m confirmation']}), ob_timeframe: 'H1', ob_kind: 'internal', ob_origin: -9 * S, confirmations: 2, ...over})
    const trades = [ob({}), ob({id: 'b', ob_timeframe: 'M15', status: 'skipped', tps: undefined}),
      ob({id: 'c', time: 4 * S, entry_time: 5 * S, ob_timeframe: 'M15', result_r: -1, tp_hit: 0})]
    const d = overlapSignalDrawings(trades, 2, 'UTC', 10 * S, S, 'newest', OB_SIGNALS)
    const labels = d.filter(x => x.kind === 'label' && x.style !== 'label_left')
    expect(labels.map(x => x.kind === 'label' ? x.text : '')).toEqual(['OB BUY · 15m+1H', 'OB BUY · 15m'])
    expect(labels[0].tooltip).toContain('OB BUY · 2 × 5m confirmation')
    expect(d.every(x => x.group === 'ob_signals' && x.id <= -200000)).toBe(true)
    const t = overlapSignalTable(trades, 'XAUUSD', '5m', 2, 'UTC', 10 * S, -1, OB_SIGNALS)!
    const byLabel = Object.fromEntries(t.rows.map(r => [r[0].text, r.slice(1).map(c => c.text)]))
    expect(t.id).toBe('ob_signals')
    expect(t.rows[0][0].text).toBe('❄ ORDER BLOCK SIGNALS')
    expect(byLabel['By OB TF'][0]).toBe('15m −1.0R (1) · 1H +1.0R (1)')
    expect(byLabel['Last Signal'][0]).toBe('OB BUY')
  })

  it('draws the waiting order blocks with their timeframe', () => {
    const z: ObZone = {id: 1, timeframe: 'H4', kind: 'swing', side: -1, top: 2010, bottom: 2005, origin: 0, known_at: S,
      touched_at: null, count: 0}
    const d = obZoneDrawings([z, {...z, id: 2, side: 1, timeframe: 'M15'}], 10 * S, S, 2, 'UTC')
    expect(d.map(x => x.kind)).toEqual(['box', 'label', 'box', 'label'])
    expect(d[1].kind === 'label' && [d[1].text, d[1].y]).toEqual(['OB 4H', 2005])
    expect(d[3].kind === 'label' && [d[3].text, d[3].y]).toEqual(['OB 15m', 2010])
    expect(new Set(d.map(x => x.id)).size).toBe(4)
  })
})
