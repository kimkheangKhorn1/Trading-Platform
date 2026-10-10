import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import type { CancelledOrder, TradeDto } from '../../api/types'
import { fmtDateTime, fmtNum, fmtPrice, fmtSigned, type TimezoneId } from '../../lib/format'
import { Icon } from '../common/Icon'

const ROW_H = 26

type SortKey = 'entry_time' | 'pnl' | 'r_multiple' | 'exit_time'

interface Col {
  key: string
  label: string
  width: number
  num?: boolean
  sort?: SortKey
  render: (t: TradeDto, i: number) => React.ReactNode
}

export function TradesTable({ trades, titles, precision, tz, selectedId, onSelect }: {
  trades: TradeDto[]
  titles: Record<string, string>
  precision: number
  tz: TimezoneId
  selectedId: string | null
  onSelect: (t: TradeDto) => void
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'entry_time', dir: 1 })
  const [scroll, setScroll] = useState(0)
  const [height, setHeight] = useState(300)
  const box = useRef<HTMLDivElement>(null)

  const rows = useMemo(() => {
    const r = [...trades]
    r.sort((a, b) => {
      const va = a[sort.key] ?? -Infinity
      const vb = b[sort.key] ?? -Infinity
      return (va < vb ? -1 : va > vb ? 1 : a.id < b.id ? -1 : 1) * sort.dir
    })
    return r
  }, [trades, sort])
  const number = useMemo(() => {
    const byEntry = [...trades].sort((a, b) => a.entry_time - b.entry_time || (a.id < b.id ? -1 : 1))
    return new Map(byEntry.map((t, i) => [t.id, i + 1]))
  }, [trades])

  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(() => setHeight(el.clientHeight))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // keep the selected trade visible when it changes from elsewhere (chart click)
  useEffect(() => {
    const el = box.current
    if (!el || !selectedId) return
    const i = rows.findIndex((t) => t.id === selectedId)
    if (i < 0) return
    const top = i * ROW_H
    if (top < el.scrollTop || top + ROW_H > el.scrollTop + el.clientHeight - ROW_H) el.scrollTop = Math.max(0, top - el.clientHeight / 2)
  }, [selectedId, rows])

  const p = (v: number | null) => fmtPrice(v, precision)
  const cols: Col[] = [
    { key: 'n', label: '#', width: 46, num: true, render: (t) => number.get(t.id) },
    { key: 'strategy', label: 'Strategy', width: 150, render: (t) => titles[t.strategy] ?? t.strategy },
    { key: 'side', label: 'Type', width: 92, render: (t) => (
      <span className={t.side === 'long' ? 'up' : 'down'}>{t.side === 'long' ? 'Long' : 'Short'} <span className="muted">{t.order_type}</span></span>) },
    { key: 'entry_time', label: 'Entry time', width: 128, sort: 'entry_time', render: (t) => fmtDateTime(t.entry_time, tz) },
    { key: 'entry', label: 'Entry', width: 84, num: true, render: (t) => p(t.entry_price) },
    { key: 'sl', label: 'Stop-loss', width: 84, num: true, render: (t) => (
      <span title={t.final_stop !== t.stop_loss ? `moved to ${p(t.final_stop)}` : undefined}>{p(t.stop_loss)}{t.final_stop !== t.stop_loss ? '*' : ''}</span>) },
    { key: 'tp1', label: 'TP1', width: 84, num: true, render: (t) => p(t.tp1) },
    { key: 'tp2', label: 'TP2', width: 84, num: true, render: (t) => p(t.tp2) },
    { key: 'exit_time', label: 'Exit time', width: 128, sort: 'exit_time', render: (t) => fmtDateTime(t.exit_time, tz) },
    { key: 'exit', label: 'Exit (avg)', width: 84, num: true, render: (t) => p(t.exit_price) },
    { key: 'units', label: 'Units', width: 62, num: true, render: (t) => fmtNum(t.units, 0) },
    { key: 'pnl', label: 'P&L', width: 90, num: true, sort: 'pnl', render: (t) => <span className={t.pnl > 0 ? 'up' : t.pnl < 0 ? 'down' : ''}>{fmtSigned(t.pnl)}</span> },
    { key: 'r', label: 'R', width: 64, num: true, sort: 'r_multiple', render: (t) => <span className={(t.r_multiple ?? 0) > 0 ? 'up' : (t.r_multiple ?? 0) < 0 ? 'down' : ''}>{t.r_multiple === null ? '—' : fmtSigned(t.r_multiple)}</span> },
    { key: 'mae', label: 'MAE / MFE (R)', width: 104, num: true, render: (t) => `${fmtNum(t.mae_r, 2)} / ${fmtNum(t.mfe_r, 2)}` },
    { key: 'bars', label: 'Bars', width: 52, num: true, render: (t) => t.bars_held ?? '—' },
    { key: 'reason', label: 'Exit reason', width: 128, render: (t) => (
      <span>{t.exit_reason}{t.ambiguous ? <span className="flag" title="Outcome depended on the intrabar price-path assumption (SL and target reachable in the same bar)"><Icon name="warning" size={12} /></span> : null}</span>) },
  ]
  const totalW = cols.reduce((a, c) => a + c.width, 0)
  const first = Math.max(0, Math.floor(scroll / ROW_H) - 5)
  const last = Math.min(rows.length, Math.ceil((scroll + height) / ROW_H) + 5)

  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const i = rows.findIndex((t) => t.id === selectedId)
    const j = Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))
    if (rows[j]) onSelect(rows[j])
  }

  if (!trades.length) return <div className="empty-state">No trades in the selected period.</div>
  return (
    <div className="vtable" ref={box} tabIndex={0} onKeyDown={onKey} onScroll={(e) => setScroll(e.currentTarget.scrollTop)}
      aria-label="List of trades (use arrow keys to step through trades)">
      <div className="vtable-inner" style={{ width: totalW, height: (rows.length + 1) * ROW_H }}>
        <div className="vrow head" style={{ width: totalW }}>
          {cols.map((c) => (
            <div key={c.key} className={`vcell ${c.num ? 'num' : ''} ${c.sort ? 'sortable' : ''}`} style={{ width: c.width }}
              onClick={() => c.sort && setSort({ key: c.sort, dir: sort.key === c.sort ? (-sort.dir as 1 | -1) : 1 })}>
              {c.label}{sort.key === c.sort ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
            </div>
          ))}
        </div>
        {rows.slice(first, last).map((t, k) => {
          const i = first + k
          return (
            <div key={t.id} className={`vrow ${t.id === selectedId ? 'sel' : ''}`} style={{ top: (i + 1) * ROW_H, width: totalW }}
              onClick={() => onSelect(t)}>
              {cols.map((c) => (
                <div key={c.key} className={`vcell ${c.num ? 'num mono' : ''}`} style={{ width: c.width }}>{c.render(t, i)}</div>
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function OrdersTable({ orders, titles, precision, tz }: { orders: CancelledOrder[]; titles: Record<string, string>; precision: number; tz: TimezoneId }) {
  if (!orders.length) return <div className="empty-state">No cancelled or expired orders.</div>
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr><th>Cancelled at</th><th>Strategy</th><th>Side</th><th>Type</th><th>Signal bar</th><th className="num">Entry</th><th className="num">Stop</th><th>Reason</th></tr>
        </thead>
        <tbody>
          {orders.slice(0, 5000).map((o) => (
            <tr key={`${o.order_id}-${o.time}`}>
              <td className="mono">{fmtDateTime(o.time, tz)}</td>
              <td>{titles[o.strategy] ?? o.strategy}</td>
              <td className={o.side === 'long' ? 'up' : 'down'}>{o.side}</td>
              <td>{o.order_type}</td>
              <td className="mono">{fmtDateTime(o.signal_time, tz)}</td>
              <td className="num mono">{fmtPrice(o.entry, precision)}</td>
              <td className="num mono">{fmtPrice(o.stop_loss, precision)}</td>
              <td>{o.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {orders.length > 5000 ? <div className="muted small pad">Showing the first 5,000 of {orders.length.toLocaleString('en-US')}.</div> : null}
    </div>
  )
}
