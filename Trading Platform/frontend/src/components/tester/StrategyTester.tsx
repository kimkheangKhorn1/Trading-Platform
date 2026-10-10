import { useEffect, useMemo } from 'react'
import type { TimeframeCode } from '../../api/types'
import { fmtDateTime, tsToDate } from '../../lib/format'
import { ALL_TFS, currentFeed, TF_LABELS, useApp } from '../../state/app'
import { useIndicators } from '../../state/indicators'
import { useTester, type TesterTab } from '../../state/tester'
import { Checkbox, Select } from '../common/Fields'
import { Icon, Spinner } from '../common/Icon'
import { Tabs } from '../common/Modal'
import { Overview } from './Overview'
import { PerformanceTable } from './PerformanceTable'
import { Properties } from './Properties'
import { OrdersTable, TradesTable } from './TradesTable'

const RANGES: { label: string; days: number | null }[] = [
  { label: '1W', days: 7 }, { label: '1M', days: 30 }, { label: '3M', days: 91 }, { label: '6M', days: 182 },
  { label: '1Y', days: 365 }, { label: 'All', days: null },
]

export function StrategyTester() {
  const t = useTester()
  const tz = useApp((s) => s.timezone)
  const chartTf = useApp((s) => s.timeframe)
  const scripts = useApp((s) => s.scripts)
  const openDialog = useApp((s) => s.openDialog)
  useApp((s) => s.feeds)
  const instances = useIndicators((s) => s.instances)
  const add = useIndicators((s) => s.add)
  const feed = currentFeed()

  const stratInstances = instances.filter((i) => scripts.find((s) => s.id === i.scriptId)?.strategies.length)
  const inst = stratInstances.find((i) => i.uid === t.instanceUid) ?? stratInstances[0]
  const meta = scripts.find((s) => s.id === inst?.scriptId)
  const strategyScript = scripts.find((s) => s.strategies.length)
  const tf: TimeframeCode = t.timeframe ?? chartTf
  const series = feed?.series.find((s) => s.timeframe === tf)

  useEffect(() => {
    if (inst && inst.uid !== t.instanceUid) t.set({ instanceUid: inst.uid })
  }, [inst, t])

  const applyRange = (days: number | null) => {
    if (!series?.last) return
    const end = series.last
    const start = days === null ? series.first ?? end : end - days * 86_400
    t.set({ start: tsToDate(start), end: tsToDate(end) })
  }

  const res = t.result
  const titles = useMemo(() => Object.fromEntries((res?.strategies ?? meta?.strategies ?? []).map((s) => [s.id, s.title])), [res, meta])
  const viewTrades = useMemo(() => (res ? (t.view === 'combined' ? res.trades : res.trades.filter((x) => x.strategy === t.view)) : []), [res, t.view])
  const viewOrders = useMemo(() => (res ? (t.view === 'combined' ? res.cancelled_orders : res.cancelled_orders.filter((x) => x.strategy === t.view)) : []), [res, t.view])
  const allStrats = meta?.strategies ?? []
  const selected = t.strategies ?? allStrats.map((s) => s.id)
  const toggleStrategy = (id: string, on: boolean) => {
    const next = on ? [...new Set([...selected, id])] : selected.filter((x) => x !== id)
    t.set({ strategies: next.length === allStrats.length ? null : next })
  }

  if (!t.open) {
    return (
      <div className="tester collapsed">
        <button className="tester-title" onClick={() => t.set({ open: true })}>
          <Icon name="flask" size={15} /> Strategy Tester <Icon name="chevronUp" size={14} />
        </button>
        {res ? <span className="muted small">Last run: {res.metrics.combined.all.total_trades} trades · net {res.metrics.combined.all.net_profit.toFixed(2)}</span> : null}
      </div>
    )
  }

  return (
    <div className="tester" style={{ height: t.height }}>
      <div className="tester-head">
        <button className="tester-title" onClick={() => t.set({ open: false })} title="Collapse">
          <Icon name="flask" size={15} /> Strategy Tester <Icon name="chevronDown" size={14} />
        </button>
        {stratInstances.length > 1 ? (
          <Select value={inst?.uid ?? ''} ariaLabel="Strategy script"
            options={stratInstances.map((i, k) => ({ value: i.uid, label: `${scripts.find((s) => s.id === i.scriptId)?.short_name} #${k + 1}` }))}
            onChange={(uid) => t.set({ instanceUid: uid })} />
        ) : meta ? <span className="strat-name">{meta.short_name}</span> : strategyScript ? (
          <button className="btn ghost sm" onClick={() => add(strategyScript)}>
            <Icon name="plus" size={14} /> Add {strategyScript.short_name} to chart
          </button>
        ) : <span className="muted small">No strategy scripts available</span>}
        {inst ? (
          <button className="icon-btn sm" title="Strategy inputs" onClick={() => openDialog({ type: 'settings', uid: inst.uid })}>
            <Icon name="gear" size={15} />
          </button>
        ) : null}
        <div className="strat-chips">
          {allStrats.map((s) => (
            <Checkbox key={s.id} checked={selected.includes(s.id)} onChange={(v) => toggleStrategy(s.id, v)} label={s.title} title={s.description} />
          ))}
        </div>
        <div className="grow" />
        <div className="range">
          <input className="input" type="date" value={t.start} aria-label="Start date (UTC)" onChange={(e) => t.set({ start: e.target.value })} />
          <span className="muted">→</span>
          <input className="input" type="date" value={t.end} aria-label="End date (UTC)" onChange={(e) => t.set({ end: e.target.value })} />
          <span className="muted small">UTC</span>
          <div className="range-btns">
            {RANGES.map((r) => (
              <button key={r.label} className="chip" disabled={!series?.last} onClick={() => applyRange(r.days)}
                title={series?.last ? `Ending at the last stored ${TF_LABELS[tf]} candle` : 'No stored data for this timeframe'}>{r.label}</button>
            ))}
          </div>
        </div>
        <Select value={t.timeframe ?? ''} ariaLabel="Backtest timeframe" width={118}
          options={[{ value: '', label: `Chart (${TF_LABELS[chartTf]})` }, ...ALL_TFS.filter((x) => !feed || feed.timeframes.includes(x)).map((x) => ({ value: x, label: TF_LABELS[x] }))]}
          onChange={(v) => t.set({ timeframe: v === '' ? null : (v as TimeframeCode) })} />
        <button className="btn primary" onClick={() => void t.run()} disabled={t.running || !inst || selected.length === 0}>
          {t.running ? <Spinner size={12} /> : <Icon name="play" size={14} />} {res ? 'Rerun' : 'Run backtest'}
        </button>
      </div>
      <div className="tester-tabs">
        <Tabs<TesterTab> value={t.tab} onChange={(tab) => t.set({ tab })} tabs={[
          { id: 'overview', label: 'Overview' }, { id: 'performance', label: 'Performance Summary' },
          { id: 'trades', label: `List of Trades${res ? ` (${viewTrades.length})` : ''}` },
          { id: 'orders', label: `Cancelled orders${res ? ` (${viewOrders.length})` : ''}` }, { id: 'properties', label: 'Properties' }]} />
        <div className="grow" />
        {res ? (
          <>
            <Select value={t.view} ariaLabel="Results view" width={190}
              options={[{ value: 'combined', label: res.strategies.length > 1 ? 'All strategies (combined)' : res.strategies[0].title },
                ...(res.strategies.length > 1 ? res.strategies.map((s) => ({ value: s.id, label: s.title })) : [])]}
              onChange={(v) => t.set({ view: v, selectedTradeId: null })} />
            <Checkbox checked={t.showTradesOnChart} onChange={(v) => t.set({ showTradesOnChart: v })} label="Trades on chart" />
          </>
        ) : null}
      </div>
      {t.error ? <div className="alert error"><Icon name="warning" size={15} /> {t.error}</div> : null}
      {res && res.warnings.length ? (
        <div className="alert warn">{res.warnings.map((w) => <div key={w}><Icon name="warning" size={13} /> {w}</div>)}</div>
      ) : null}
      <div className="tester-body">
        {t.tab === 'properties' ? <Properties /> : null}
        {t.tab !== 'properties' && !res ? (
          <div className="empty-state">
            {t.running ? <><Spinner /> Running backtest…</> : !inst ? (
              <p className="muted">The Strategy Tester runs scripts that emit trade signals. None is on the chart{strategyScript ? '' : ', and no such script exists in the project yet'}.</p>
            ) : (
              <>
                <p>Choose a date range and press <b>Run backtest</b>.</p>
                <p className="muted small">Execution assumptions are on the Properties tab. Results include only trades whose signals fall inside the range; earlier bars are used as indicator warm-up.</p>
              </>
            )}
          </div>
        ) : null}
        {res && t.tab === 'overview' ? <Overview result={res} view={t.view} tz={tz} /> : null}
        {res && t.tab === 'performance' ? (
          <PerformanceTable block={t.view === 'combined' ? res.metrics.combined : res.metrics.per_strategy[t.view]} />
        ) : null}
        {res && t.tab === 'trades' ? (
          <TradesTable trades={viewTrades} titles={titles} precision={res.data.feed.precision} tz={tz}
            selectedId={t.selectedTradeId} onSelect={(tr) => t.selectTrade(tr)} />
        ) : null}
        {res && t.tab === 'orders' ? <OrdersTable orders={viewOrders} titles={titles} precision={res.data.feed.precision} tz={tz} /> : null}
      </div>
      {res ? (
        <div className="tester-foot muted small">
          {res.data.feed.display_name} · {TF_LABELS[res.request.timeframe]} · {fmtDateTime(res.summary.first_bar, tz)} → {fmtDateTime(res.summary.last_bar, tz)} ·
          {' '}{res.summary.bars.toLocaleString('en-US')} bars + {res.summary.warmup_bars.toLocaleString('en-US')} warm-up · {res.summary.signals} signals ·
          {' '}spread {res.assumptions.spread} · slippage {res.assumptions.slippage} · commission {res.assumptions.commission_per_lot}/lot ·
          {' '}{res.assumptions.intrabar_policy === 'conservative' ? 'conservative intrabar path' : 'TradingView intrabar heuristic'}
          {' '}({res.summary.ambiguous_bars} ambiguous bars) · {res.summary.total_ms.toFixed(0)} ms
        </div>
      ) : null}
    </div>
  )
}
