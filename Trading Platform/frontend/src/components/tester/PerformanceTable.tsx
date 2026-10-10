import type { MetricsBlock, TradeStats } from '../../api/types'
import { fmtMoney, fmtNum, fmtPct, fmtSigned } from '../../lib/format'

type Row = [string, (s: TradeStats) => string, boolean?]

const ROWS: Row[] = [
  ['Net profit', (s) => fmtSigned(s.net_profit)],
  ['Net profit %', (s) => fmtPct(s.net_profit_pct)],
  ['Gross profit', (s) => fmtMoney(s.gross_profit)],
  ['Gross loss', (s) => fmtMoney(s.gross_loss)],
  ['Max drawdown', (s) => (s.max_drawdown === undefined ? '' : `${fmtMoney(s.max_drawdown)} (${fmtPct(s.max_drawdown_pct)})`), true],
  ['Max run-up', (s) => (s.max_runup === undefined ? '' : fmtMoney(s.max_runup)), true],
  ['Return / max drawdown', (s) => (s.return_over_max_dd === undefined ? '' : fmtNum(s.return_over_max_dd)), true],
  ['Profit factor', (s) => fmtNum(s.profit_factor, 3)],
  ['Commission paid', (s) => fmtMoney(s.commission_paid)],
  ['Total closed trades', (s) => String(s.total_trades)],
  ['Open trades', (s) => String(s.open_trades)],
  ['Winning trades', (s) => String(s.winning_trades)],
  ['Losing trades', (s) => String(s.losing_trades)],
  ['Breakeven trades', (s) => String(s.breakeven_trades)],
  ['Percent profitable', (s) => fmtPct(s.win_rate)],
  ['Avg trade', (s) => fmtSigned(s.avg_trade)],
  ['Avg winning trade', (s) => fmtMoney(s.avg_win)],
  ['Avg losing trade', (s) => fmtMoney(s.avg_loss)],
  ['Ratio avg win / avg loss', (s) => fmtNum(s.win_loss_ratio, 3)],
  ['Largest winning trade', (s) => fmtMoney(s.largest_win)],
  ['Largest losing trade', (s) => fmtMoney(s.largest_loss)],
  ['Avg R-multiple', (s) => (s.avg_r === null ? '—' : `${fmtSigned(s.avg_r)}R`)],
  ['Total R', (s) => (s.total_r === null ? '—' : `${fmtSigned(s.total_r)}R`)],
  ['Avg planned reward : risk', (s) => (s.avg_planned_rr === null ? '—' : `1 : ${fmtNum(s.avg_planned_rr)}`)],
  ['Max consecutive wins', (s) => String(s.max_consecutive_wins)],
  ['Max consecutive losses', (s) => String(s.max_consecutive_losses)],
  ['Avg # bars in trades', (s) => fmtNum(s.avg_bars_in_trade, 1)],
  ['Trades decided by intrabar assumption', (s) => String(s.ambiguous_trades)],
]

export function PerformanceTable({ block }: { block: MetricsBlock }) {
  return (
    <div className="table-scroll">
      <table className="perf-table">
        <thead>
          <tr><th /><th className="num">All</th><th className="num">Long</th><th className="num">Short</th></tr>
        </thead>
        <tbody>
          {ROWS.map(([label, f, allOnly]) => (
            <tr key={label}>
              <th>{label}</th>
              <td className="num mono">{f(block.all)}</td>
              <td className="num mono">{allOnly ? '' : f(block.long)}</td>
              <td className="num mono">{allOnly ? '' : f(block.short)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
