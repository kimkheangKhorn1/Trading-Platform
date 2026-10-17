import { useMemo } from 'react'
import type { BacktestResult, SeriesPoints, TradeDto } from '../../api/types'
import { fmtMoney, fmtNum, fmtPct, fmtSigned, type TimezoneId } from '../../lib/format'
import { EquityChart } from './EquityChart'

function Kpi({ label, value, sub, tone, title }: { label: string; value: string; sub?: string; tone?: 'up' | 'down' | ''; title?: string }) {
  return (
    <div className="kpi" title={title}>
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${tone ?? ''}`}>{value}</div>
      {sub ? <div className="kpi-sub">{sub}</div> : null}
    </div>
  )
}

function Distribution({ trades }: { trades: TradeDto[] }) {
  const bins = useMemo(() => {
    const rs = trades.map((t) => t.r_multiple).filter((r): r is number => r !== null)
    const lo = -3
    const hi = 5
    const step = 0.5
    const n = Math.round((hi - lo) / step)
    const counts = new Array(n).fill(0)
    for (const r of rs) counts[Math.min(n - 1, Math.max(0, Math.floor((r - lo) / step)))]++
    return { counts, lo, step, total: rs.length }
  }, [trades])
  const max = Math.max(1, ...bins.counts)
  return (
    <div className="dist">
      <div className="dist-title">Trade distribution (R-multiple) <span className="muted">· {bins.total} trades</span></div>
      <div className="dist-bars" role="img" aria-label="Histogram of trade R-multiples">
        {bins.counts.map((c, i) => {
          const from = bins.lo + i * bins.step
          return (
            <div key={i} className="dist-col" title={`${from.toFixed(1)}R to ${(from + bins.step).toFixed(1)}R: ${c} trades`}>
              {c ? <span className="dist-count">{c}</span> : null}
              <div className={`dist-bar ${from < 0 ? 'neg' : 'pos'}`} style={{ height: `${(c / max) * 100}%` }} />
            </div>
          )
        })}
      </div>
      <div className="dist-axis">
        {[-3, -2, -1, 0, 1, 2, 3, 4, 5].map((r) => <span key={r}>{r}R</span>)}
      </div>
    </div>
  )
}

export function Overview({ result, view, tz }: { result: BacktestResult; view: string; tz: TimezoneId }) {
  const block = view === 'combined' ? result.metrics.combined : result.metrics.per_strategy[view]
  const m = block.all
  const trades = view === 'combined' ? result.trades : result.trades.filter((t) => t.strategy === view)
  const cap = result.assumptions.initial_capital
  const equity: SeriesPoints = view === 'combined' ? result.equity.combined : result.equity.per_strategy[view]
  const extra = useMemo(
    () => (view === 'combined' && result.strategies.length > 1
      ? result.strategies.map((s) => ({ title: s.title, series: result.equity.per_strategy[s.id] }))
      : []),
    [result, view],
  )
  const drawdown = useMemo<SeriesPoints>(() => {
    const values: number[] = []
    for (let i = 0, peak = cap; i < equity.values.length; i++) {
      peak = Math.max(peak, equity.values[i])
      values.push(equity.values[i] - peak)
    }
    return { times: equity.times, values }
  }, [equity, cap])
  const tone = (v: number | null | undefined) => (v === null || v === undefined || v === 0 ? '' : v > 0 ? 'up' : 'down')
  return (
    <div className="overview">
      <div className="kpis">
        <Kpi label="Net profit" value={fmtSigned(m.net_profit)} sub={fmtPct(m.net_profit_pct)} tone={tone(m.net_profit)} />
        <Kpi label="Gross profit" value={fmtMoney(m.gross_profit)} tone="up" />
        <Kpi label="Gross loss" value={fmtMoney(m.gross_loss)} tone={m.gross_loss < 0 ? 'down' : ''} />
        <Kpi label="Total trades" value={String(m.total_trades)} sub={m.open_trades ? `${m.open_trades} open` : `${m.winning_trades} W · ${m.losing_trades} L · ${m.breakeven_trades} BE`} />
        <Kpi label="Win rate" value={fmtPct(m.win_rate)} sub={`${m.winning_trades} / ${m.total_trades}`} />
        <Kpi label="TP win rate" value={fmtPct(m.tp_win_rate ?? null)} sub={`${m.tp_wins ?? 0} TP · ${m.sl_losses ?? 0} SL`}
          title="Win = any take-profit hit (TP1, TP2 or TP3). Loss = stop-loss hit before any take-profit." />
        <Kpi label="Profit factor" value={m.profit_factor === null ? '—' : fmtNum(m.profit_factor, 3)}
          title={m.profit_factor === null ? 'Undefined (no losing trades)' : 'Gross profit / |gross loss|'} />
        <Kpi label="Max drawdown" value={fmtMoney(m.max_drawdown ?? 0)} sub={fmtPct(m.max_drawdown_pct ?? 0)} tone={(m.max_drawdown ?? 0) > 0 ? 'down' : ''}
          title="Largest peak-to-trough fall of bar-close mark-to-market equity" />
        <Kpi label="Avg trade" value={fmtSigned(m.avg_trade)} tone={tone(m.avg_trade)} />
        <Kpi label="Avg R-multiple" value={m.avg_r === null ? '—' : `${fmtSigned(m.avg_r)}R`} tone={tone(m.avg_r)}
          title="Average realised net P&L divided by initial risk (entry fill to initial stop)" />
        <Kpi label="Avg planned R:R" value={m.avg_planned_rr === null ? '—' : `1 : ${fmtNum(m.avg_planned_rr)}`}
          title="Average reward-to-risk of the targets as planned by the strategy (weighted by TP size)" />
      </div>
      <div className="overview-charts">
        <EquityChart equity={equity} drawdown={drawdown} extra={extra} capital={cap} tz={tz} />
        <Distribution trades={trades} />
      </div>
    </div>
  )
}
