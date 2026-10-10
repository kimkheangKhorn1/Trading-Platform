import type { ExecutionSettings } from '../../api/types'
import { useApp } from '../../state/app'
import { useIndicators } from '../../state/indicators'
import { DEFAULT_EXECUTION, useTester } from '../../state/tester'
import { Checkbox, NumberField, Select } from '../common/Fields'

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="prop-row">
      <div className="prop-label">
        {label}
        {hint ? <div className="prop-hint">{hint}</div> : null}
      </div>
      <div className="prop-ctl">{children}</div>
    </div>
  )
}

export function Properties() {
  const ex = useTester((s) => s.execution)
  const setEx = useTester((s) => s.setExecution)
  const warmup = useTester((s) => s.warmupBars)
  const set = useTester((s) => s.set)
  const instanceUid = useTester((s) => s.instanceUid)
  const instances = useIndicators((s) => s.instances)
  const scripts = useApp((s) => s.scripts)
  const openDialog = useApp((s) => s.openDialog)
  const inst = instances.find((i) => i.uid === instanceUid) ?? instances.find((i) => scripts.find((s) => s.id === i.scriptId)?.strategies.length)
  const meta = scripts.find((s) => s.id === inst?.scriptId)
  const num = (k: keyof ExecutionSettings, opts: { min?: number; max?: number; step?: number; integer?: boolean } = {}) => (
    <NumberField value={ex[k] as number} min={opts.min} max={opts.max} step={opts.step} integer={opts.integer}
      onChange={(v) => v !== null && setEx({ [k]: v } as Partial<ExecutionSettings>)} />
  )
  const sizingLabel = { fixed_units: 'units', fixed_lots: 'lots', risk_percent: '% of equity', risk_cash: 'USD risk' }[ex.sizing_mode]

  return (
    <div className="properties">
      <div className="prop-col">
        <h4 className="group-title">Strategy</h4>
        <Row label="Script" hint="The tester runs the strategies of this chart indicator with its current inputs.">
          <span>{meta ? meta.name : 'none on chart'}</span>
          {inst ? <button className="btn ghost sm" onClick={() => openDialog({ type: 'settings', uid: inst.uid })}>Edit inputs…</button> : null}
        </Row>
        <Row label="Warm-up bars" hint="Bars before the start date fed to the indicator only (never traded). Empty = script default.">
          <NumberField value={warmup} allowNull integer min={0} max={100000} placeholder={meta ? String(meta.default_warmup) : 'auto'}
            onChange={(v) => set({ warmupBars: v })} />
        </Row>
        <h4 className="group-title">Capital & sizing</h4>
        <Row label="Initial capital (USD)">{num('initial_capital', { min: 1 })}</Row>
        <Row label="Position sizing" hint="Evaluated when the entry fills, from the strategy's realised equity.">
          <Select value={ex.sizing_mode} onChange={(v) => setEx({ sizing_mode: v })} options={[
            { value: 'risk_percent', label: 'Risk % of equity' }, { value: 'risk_cash', label: 'Fixed cash risk' },
            { value: 'fixed_units', label: 'Fixed units (oz)' }, { value: 'fixed_lots', label: 'Fixed lots' }]} />
          {num('sizing_value', { min: 0.0001 })} <span className="muted small">{sizingLabel}</span>
        </Row>
        <Row label="Contract size" hint="Units per lot (100 oz for XAUUSD).">{num('contract_size', { min: 0.0001 })}</Row>
        <Row label="Unit step / minimum" hint="Sizes are rounded down; orders below the minimum are cancelled, never rounded up.">
          {num('unit_step', { min: 0.0001 })} {num('min_units', { min: 0.0001 })}
        </Row>
        <Row label="Max units (optional)">
          <NumberField value={ex.max_units} allowNull min={0} onChange={(v) => setEx({ max_units: v })} placeholder="no cap" />
        </Row>
        <Row label="Max open trades / strategy">{num('max_open_trades', { min: 1, integer: true })}</Row>
      </div>
      <div className="prop-col">
        <h4 className="group-title">Costs</h4>
        <Row label="Candle prices are" hint="OANDA feed = mid. Most CSV exports are bid prices.">
          <Select value={ex.price_basis} onChange={(v) => setEx({ price_basis: v })}
            options={[{ value: 'mid', label: 'Mid' }, { value: 'bid', label: 'Bid' }, { value: 'ask', label: 'Ask' }]} />
        </Row>
        <Row label="Spread (USD)" hint="Buys fill at the ask, sells at the bid; triggers use the executing side.">{num('spread', { min: 0, step: 0.01 })}</Row>
        <Row label="Slippage (USD)" hint="Adverse, on market and stop fills (stop-losses included).">{num('slippage', { min: 0, step: 0.01 })}</Row>
        <Row label="Commission per lot, per side (USD)">{num('commission_per_lot', { min: 0, step: 0.01 })}</Row>
        <h4 className="group-title">Execution</h4>
        <Row label="Intrabar price path" hint={ex.intrabar_policy === 'conservative'
          ? 'Simulates O-H-L-C and O-L-H-C for every bar and keeps the worse outcome (stop before target when both are reachable).'
          : 'TradingView broker emulator: O-H-L-C if the open is closer to the high, else O-L-H-C.'}>
          <Select value={ex.intrabar_policy} onChange={(v) => setEx({ intrabar_policy: v })}
            options={[{ value: 'conservative', label: 'Conservative (worst case)' }, { value: 'ohlc_heuristic', label: 'TradingView heuristic' }]} />
        </Row>
        <Row label="Order handling">
          <div className="checks">
            <Checkbox checked={ex.replace_pending} onChange={(v) => setEx({ replace_pending: v })} label="New signal replaces the strategy's pending orders" />
            <Checkbox checked={ex.close_on_opposite} onChange={(v) => setEx({ close_on_opposite: v })} label="Opposite signal closes open trades at next open" />
            <Checkbox checked={ex.close_at_end} onChange={(v) => setEx({ close_at_end: v })} label="Close open trades at the end of the window" />
          </div>
        </Row>
        <div className="prop-actions">
          <button className="btn ghost sm" onClick={() => setEx(DEFAULT_EXECUTION)}>Reset to defaults</button>
        </div>
      </div>
      <div className="prop-col rules">
        <h4 className="group-title">Execution rules (always applied)</h4>
        <ul>
          <li>Signals are evaluated on the close of confirmed bars only; orders become active on the next bar. Nothing fills on the signal bar.</li>
          <li>Market orders fill at the next bar's open (ask for buys, bid for sells) plus slippage.</li>
          <li>Limit orders fill at their price; only a gap open through the price fills at the better open.</li>
          <li>Stops gapped through fill at the open (worse than the stop), plus slippage.</li>
          <li>Inside a bar, orders trigger in the order the modelled price path reaches them, so TP1 → stop to breakeven → breakeven hit can all happen in one bar.</li>
          <li>Warm-up bars before the start date feed the indicator but never create trades; only signals inside the window are traded.</li>
          <li>Each strategy is an independent book with its own equity; "Combined" sums their P&amp;L.</li>
        </ul>
      </div>
    </div>
  )
}
