import type { OverlapHover } from './badgeOverlap'
// Types mirroring the FastAPI responses. Times are UTC unix seconds (candle open time).

export type TimeframeCode = 'M1' | 'M5' | 'M15' | 'M30' | 'H1' | 'H4' | 'D'

export interface TimeframeInfo {
  code: TimeframeCode
  label: string
  seconds: number
}

export interface Health {
  status: string
  time: number
  market_data_provider: 'exness' | 'oanda'
  exness: { enabled: boolean; ready: boolean; missing: string[]; instruments: string[];
    price_type: 'bid' | 'ask'; alignment: 'utc' | 'ny17'; last_error: string | null }
  oanda: {
    enabled: boolean
    environment: string
    instruments: string[]
    price_component: string
    last_error: string | null
  }
  timeframes: TimeframeInfo[]
}

export interface SeriesSummary {
  timeframe: TimeframeCode
  origin: 'native' | 'resampled' | 'tick_derived'
  oldest_reached: boolean
  count: number
  first: number | null
  last: number | null
}

export interface ExnessHistoryStatus {
  status: 'not_downloaded' | 'downloading' | 'processing' | 'attention' | 'ready'
  symbol: string
  archives_total: number
  archives_downloaded: number
  archives_processed: number
  ticks: number
  M1: number
  M5: number
  first_tick_utc: string | null
  last_tick_utc: string | null
  errors: string[]
  missing_weekdays?: string[]
  verification?: string
}

export interface Feed {
  id: string
  provider: 'exness' | 'exness_archive' | 'oanda' | 'csv'
  symbol: string
  instrument: string | null
  price_type: 'mid' | 'bid' | 'ask'
  display_name: string
  precision: number
  alignment: 'ny17' | 'utc'
  meta: Record<string, unknown>
  series: SeriesSummary[]
  timeframes: TimeframeCode[]
  live: boolean
}

/** [time, open, high, low, close, volume, complete(0|1)] */
export type BarRow = [number, number, number, number, number, number, number]

export interface CandlesResponse {
  feed_id: string
  timeframe: TimeframeCode
  precision: number
  bars: BarRow[]
  has_more: boolean
  has_newer: boolean
  warning: string | null
}

export type InputType = 'int' | 'float' | 'bool' | 'string' | 'color' | 'source' | 'timeframe'
export type InputValue = number | boolean | string

export interface InputSpec {
  id: string
  type: InputType
  title: string
  default: InputValue
  minval: number | null
  maxval: number | null
  step: number | null
  options: InputValue[] | null
  group: string | null
  inline: string | null
  tooltip: string | null
  /** plain-language explanation and a worked example, shown in the settings help card */
  help?: string | null
  example?: string | null
  /** earlier defaults: a saved setting still holding one is moved to the current default */
  replaced_defaults?: InputValue[] | null
}

export type LineStyle = 'solid' | 'dashed' | 'dotted'

export interface PlotSpec {
  id: string
  title: string
  color: string
  linewidth: number
  style: 'line' | 'stepline' | 'histogram' | 'circles' | 'cross' | 'area'
  linestyle: LineStyle
  pane: 'overlay' | 'separate'
  visible: boolean
  replaced_colors?: string[] | null // earlier default colors: a saved style still at one moves to `color` once
  tooltip?: string // hover text of the line
  pane_group?: string // separate plots of different groups get their own panes, titled with the group
}

export interface ShapeSpec {
  id: string
  title: string
  shape: string
  location: 'abovebar' | 'belowbar' | 'absolute'
  color: string
  text: string
  textcolor: string
  size: string
  visible: boolean
  replaced_colors?: string[] | null
}

export interface StrategySpec {
  id: string
  title: string
  description: string
}

export interface DrawingGroupSpec {
  id: string
  title: string
  visible: boolean
}

export interface ScriptMeta {
  id: string
  name: string
  short_name: string
  overlay: boolean
  description: string
  category: string
  inputs: InputSpec[]
  plots: PlotSpec[]
  shapes: ShapeSpec[]
  strategies: StrategySpec[]
  drawing_groups: DrawingGroupSpec[]
  default_warmup: number
  /** Pine indicator(behind_chart): false draws the script's boxes in front of the candles */
  behind_chart?: boolean
  /** false: labels and markers stay where the script put them (no collision layout) */
  label_layout?: boolean
  /** inputs that only change what is drawn (calculations ignore them) */
  display_inputs?: string[]
  /** present when the script can be tuned (see TuningDialog) */
  tuning?: TuningSpec | null
}

// ---------------------------------------------------------------- tuning
export interface TuningParamSpec {
  id: string
  min?: number
  max?: number
  step?: number
  options?: InputValue[]
  /** tuned by default */
  on?: boolean
}

export interface TuningSpec {
  score: string
  note?: string
  params: TuningParamSpec[]
}

export type TuningObjective = 'rule' | 'split' | 'hold'

export interface TuningMetrics {
  trades: number
  wins: number
  losses: number
  tp1: number
  tp2: number
  tp3: number
  win_rate: number
  rule: number
  rule_h1: number
  rule_h2: number
  split: number
  split_h1: number
  split_h2: number
  hold: number
  hold_h1: number
  hold_h2: number
}

export interface TuneResult {
  n: number
  params: Record<string, InputValue>
  metrics: TuningMetrics
  qualified: boolean
}

export interface TuningRequest {
  feed_id: string
  timeframe: TimeframeCode
  script_id: string
  inputs: Record<string, InputValue>
  start: number
  end: number
  min_trades: number
  tunes: number
  objective: TuningObjective
  ranges: { id: string; min?: number; max?: number; step?: number; options?: InputValue[] }[]
}

export interface TuningJob {
  id: string
  status: 'running' | 'done' | 'stopped' | 'error'
  error: string | null
  created: number
  finished: number | null
  request: TuningRequest
  objective: TuningObjective
  objective_text: string
  objectives: Record<TuningObjective, string>
  done: number
  budget: number
  total_space: string
  total_space_float: number
  exhaustive: boolean
  phase: string
  bars: number
  warmup: number
  dims: { id: string; count: number; first: InputValue; last: InputValue }[]
  base: Record<string, InputValue>
  best: TuneResult | null
  baseline: TuneResult | null
  top: TuneResult[]
  recent: TuneResult[]
  /** [tune number, score, qualified 0/1] for every finished tune */
  points: [number, number, number][]
  /** each time a tune became the best: prev = the tune it beat */
  events: { n: number; prev: number | null; prev_score: number | null; score: number; at: number }[]
}

// ---------------------------------------------------------------- dashboard panels
export interface ConfluencePanelData {
  kind: 'triple_confluence'
  id: string
  position: IndicatorTable['position']
  window: number
  engines: { key: string; name: string; bull: number | null; bear: number | null; tip: string; trend?: number }[]
  market: { rsi: number | null; adx: number | null; adx_min: number; adx_filter: boolean; htf: 'bull' | 'bear' | null }
  trade: null | {
    long: boolean; entry: number; sl: number; tps: number[]; hits: boolean[]; live_r: number | null
    open_time: number; bars: number
  }
  stats: Omit<TuningMetrics, 'rule_h1' | 'rule_h2' | 'split_h1' | 'split_h2' | 'hold_h1' | 'hold_h2'> & {
    skipped: number; since: number | null; first: number | null
    recent: { long: boolean; hits: number; outcome: 'tp3' | 'sl'; r: number; time: number }[]
  }
  precision: number
  entry_mode: string
  tips: Record<string, string>
  score: Record<TuningObjective, string>
}

export type IndicatorPanel = ConfluencePanelData

export interface BoxObj {
  kind: 'box'
  id: number
  left: number
  right: number
  top: number
  bottom: number
  border_color: string
  border_width: number
  border_style: string
  bg_color: string
  extend: 'none' | 'left' | 'right' | 'both'
  text: string
  text_color: string
  text_size: string
  text_halign: string
  text_valign: string
  tooltip: string
  tooltip_title?: string // hover card heading; the indicator name when empty
  group: string
  created_at: number
  overlap?: OverlapHover
}

export interface LineObj {
  kind: 'line'
  id: number
  x1: number
  y1: number
  x2: number
  y2: number
  color: string
  width: number
  style: string
  extend: 'none' | 'left' | 'right' | 'both'
  tooltip: string
  tooltip_title?: string // hover card heading; the indicator name when empty
  group: string
  created_at: number
  overlap?: OverlapHover
}

export interface LabelObj {
  kind: 'label'
  id: number
  x: number
  y: number
  text: string
  yloc: 'price' | 'abovebar' | 'belowbar'
  style: string
  color: string
  text_color: string
  size: string
  tooltip: string
  tooltip_title?: string // hover card heading; the indicator name when empty
  group: string
  created_at: number
  overlap?: OverlapHover
}

export type DrawingObj = BoxObj | LineObj | LabelObj

export interface ShapePoint {
  time: number
  text: string | null
  color: string | null
  tooltip: string | null
  price: number | null
}

export interface TakeProfitDto {
  price: number
  fraction: number
}

export interface IntentDto {
  id: string
  strategy: string
  side: 'long' | 'short'
  order_type: 'market' | 'limit' | 'stop'
  stop_loss: number
  take_profits: TakeProfitDto[]
  entry: number | null
  expiry_bars: number | null
  comment: string
  bar_index: number
  time: number
}

export interface TableCell {
  text: string
  bg_color: string
  text_color: string
  tooltip: string
  text_size?: string
  text_halign?: 'left' | 'center' | 'right'
  text_valign?: 'top' | 'center' | 'bottom'
}

/** A Pine ``table`` as it stands after the last bar. */
export interface IndicatorTable {
  id: string
  position: 'top_left' | 'top_center' | 'top_right' | 'middle_left' | 'middle_center' | 'middle_right'
    | 'bottom_left' | 'bottom_center' | 'bottom_right'
  rows: TableCell[][]
  /** Pine table.new styling (absent on older tables: plain cells) */
  bg_color?: string | null
  frame_color?: string | null
  frame_width?: number
  border_color?: string | null
  border_width?: number
}

export interface IndicatorOutput {
  times: number[]
  plots: Record<string, (number | null)[]>
  plot_colors: Record<string, [number, string][]>
  shapes: Record<string, ShapePoint[]>
  bar_colors: [number, string][]
  /** plotcandle colors: [time, body (null = hollow), wick, border] */
  candle_colors: [number, string | null, string, string][]
  tables: IndicatorTable[]
  /** purpose-built dashboards (by kind) */
  panels?: IndicatorPanel[]
  warnings: string[]
  drawings: DrawingObj[]
  signals: IntentDto[]
  bars_processed: number
  last_confirmed_time: number | null
  provisional_bar: boolean
  warmup_bars: number
  script_id: string
  inputs: Record<string, InputValue>
}

// ---------------------------------------------------------------- backtests
export interface ExecutionSettings {
  initial_capital: number
  price_basis: 'mid' | 'bid' | 'ask'
  spread: number
  slippage: number
  commission_per_lot: number
  contract_size: number
  sizing_mode: 'fixed_units' | 'fixed_lots' | 'risk_percent' | 'risk_cash'
  sizing_value: number
  unit_step: number
  min_units: number
  max_units: number | null
  intrabar_policy: 'conservative' | 'ohlc_heuristic'
  max_open_trades: number
  replace_pending: boolean
  close_on_opposite: boolean
  close_at_end: boolean
}

export interface BacktestRequest {
  feed_id: string
  timeframe: TimeframeCode
  start: number
  end: number
  script_id: string
  inputs: Record<string, InputValue>
  strategies: string[] | null
  execution: ExecutionSettings
  warmup_bars: number | null
}

export interface FillDto {
  index: number
  time: number
  price: number
  units: number
  kind: string
}

export interface TradeDto {
  id: string
  strategy: string
  side: 'long' | 'short'
  order_type: string
  comment: string
  signal_time: number
  entry_time: number
  entry_price: number
  units: number
  stop_loss: number
  final_stop: number
  take_profits: number[]
  tp1: number | null
  tp2: number | null
  exit_time: number | null
  exit_price: number | null
  gross_pnl: number
  commission: number
  pnl: number
  r_multiple: number | null
  planned_rr: number | null
  risk_cash: number | null
  exit_reason: string
  is_open: boolean
  fills: FillDto[]
  mae: number
  mfe: number
  mae_r: number | null
  mfe_r: number | null
  bars_held: number | null
  ambiguous: boolean
  tps_hit?: number
  outcome?: 'win' | 'loss' | 'open' | 'none'
}

export interface TradeStats {
  total_trades: number
  open_trades: number
  winning_trades: number
  losing_trades: number
  breakeven_trades: number
  win_rate: number | null
  net_profit: number
  net_profit_pct: number
  gross_profit: number
  gross_loss: number
  profit_factor: number | null
  avg_trade: number | null
  avg_win: number | null
  avg_loss: number | null
  win_loss_ratio: number | null
  largest_win: number | null
  largest_loss: number | null
  avg_r: number | null
  total_r: number | null
  avg_planned_rr: number | null
  max_consecutive_wins: number
  max_consecutive_losses: number
  avg_bars_in_trade: number | null
  commission_paid: number
  ambiguous_trades: number
  // target-based outcome: any TP filled = win, SL before any TP = loss
  tp_wins?: number
  sl_losses?: number
  tp_win_rate?: number | null
  tp1_hits?: number
  tp2_hits?: number
  tp3_hits?: number
  // only on "all"
  max_drawdown?: number
  max_drawdown_pct?: number
  max_runup?: number
  final_equity?: number
  return_pct?: number
  return_over_max_dd?: number | null
}

export interface MetricsBlock {
  all: TradeStats
  long: TradeStats
  short: TradeStats
}

export interface SeriesPoints {
  times: number[]
  values: number[]
}

export interface CancelledOrder {
  order_id: string
  strategy: string
  side: string
  order_type: string
  signal_time: number
  entry: number | null
  stop_loss: number
  time: number
  reason: string
}

export interface BacktestResult {
  summary: {
    first_bar: number
    last_bar: number
    bars: number
    warmup_bars: number
    signals: number
    rejected_signals: number
    ambiguous_bars: number
    script_ms: number
    simulation_ms: number
    total_ms: number
  }
  strategies: { id: string; title: string }[]
  metrics: { combined: MetricsBlock; per_strategy: Record<string, MetricsBlock> }
  trades: TradeDto[]
  cancelled_orders: CancelledOrder[]
  equity: { combined: SeriesPoints; drawdown: SeriesPoints; per_strategy: Record<string, SeriesPoints> }
  assumptions: ExecutionSettings
  warnings: string[]
  request: BacktestRequest
  data: { feed: Feed; timeframe: TimeframeCode; stored_first: number | null; stored_last: number | null; warmup_requested: number }
}

export interface BacktestJob {
  id: string
  status: 'running' | 'done' | 'error'
  created_at: number
  request: BacktestRequest
  error: string | null
  result?: BacktestResult
}

export interface ImportReport {
  rows_read: number
  rows_imported: number
  rejected_invalid_ohlc: number
  rejected_unparsable: number
  rejected_misaligned: number
  duplicates_dropped: number
  timeframe: string | null
  first_time: number | null
  last_time: number | null
  warnings: string[]
}

export interface DownloadJob {
  id: string
  feed_id: string
  timeframe: string
  until: number
  status: 'running' | 'done' | 'error'
  added: number
  oldest: number | null
  error: string | null
  started_at: number
}

export type LiveState = 'connecting' | 'live' | 'reconnecting' | 'offline' | 'static' | 'error' | 'disconnected'
