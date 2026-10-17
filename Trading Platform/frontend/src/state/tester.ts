import { create } from 'zustand'
import { api, ApiError } from '../api/client'
import type { BacktestResult, ExecutionSettings, TimeframeCode, TradeDto } from '../api/types'
import { dateToTs } from '../lib/format'
import { load, save } from '../lib/storage'
import { useApp } from './app'
import { useIndicators } from './indicators'

export type TesterTab = 'overview' | 'performance' | 'trades' | 'orders' | 'properties'

export const DEFAULT_EXECUTION: ExecutionSettings = {
  initial_capital: 10_000,
  price_basis: 'mid',
  spread: 0.3,
  slippage: 0.05,
  commission_per_lot: 0,
  contract_size: 100,
  sizing_mode: 'risk_percent',
  sizing_value: 1,
  unit_step: 1,
  min_units: 1,
  max_units: null,
  intrabar_policy: 'conservative',
  max_open_trades: 1,
  replace_pending: true,
  close_on_opposite: false,
  close_at_end: true,
}

interface Persisted {
  open: boolean
  height: number
  start: string
  end: string
  timeframe: TimeframeCode | null
  execution: ExecutionSettings
  warmupBars: number | null
  strategies: string[] | null
  showTradesOnChart: boolean
}

interface TesterState extends Persisted {
  tab: TesterTab
  instanceUid: string | null
  running: boolean
  jobId: string | null
  error: string | null
  result: BacktestResult | null
  view: string // 'combined' or a strategy id
  selectedTradeId: string | null
  set: (patch: Partial<TesterState>) => void
  setExecution: (patch: Partial<ExecutionSettings>) => void
  run: () => Promise<void>
  restoreLatest: () => Promise<void>
  selectTrade: (t: TradeDto | null) => void
}

const KEY = 'tp.tester.v1'
const today = new Date().toISOString().slice(0, 10)
const monthAgo = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10)
const persisted = load<Persisted>(KEY, {
  open: true,
  height: 380,
  start: monthAgo,
  end: today,
  timeframe: null,
  execution: DEFAULT_EXECUTION,
  warmupBars: null,
  strategies: null,
  showTradesOnChart: true,
})
persisted.execution = { ...DEFAULT_EXECUTION, ...persisted.execution }

function persist(s: TesterState) {
  const { open, height, start, end, timeframe, execution, warmupBars, strategies, showTradesOnChart } = s
  save(KEY, { open, height, start, end, timeframe, execution, warmupBars, strategies, showTradesOnChart })
}

export const useTester = create<TesterState>((set, get) => ({
  ...persisted,
  tab: 'overview',
  instanceUid: null,
  running: false,
  jobId: null,
  error: null,
  result: null,
  view: 'combined',
  selectedTradeId: null,

  set: (patch) => {
    set(patch)
    persist(get())
  },
  setExecution: (patch) => {
    set({ execution: { ...get().execution, ...patch } })
    persist(get())
  },

  run: async () => {
    const app = useApp.getState()
    const st = get()
    const instances = useIndicators.getState().instances
    const scripts = app.scripts
    const inst =
      instances.find((i) => i.uid === st.instanceUid) ??
      instances.find((i) => scripts.find((s) => s.id === i.scriptId)?.strategies.length)
    if (!inst) {
      set({ error: 'Add an indicator that emits strategy signals to the chart first (Indicators ▸ Example: EMA Cross).' })
      return
    }
    if (!app.feedId) {
      set({ error: 'No data feed selected.' })
      return
    }
    const start = dateToTs(st.start)
    const end = dateToTs(st.end, true)
    if (!(end > start)) {
      set({ error: 'The end date must be after the start date.' })
      return
    }
    set({ running: true, error: null, instanceUid: inst.uid })
    try {
      const { id } = await api.submitBacktest({
        feed_id: app.feedId,
        timeframe: st.timeframe ?? app.timeframe,
        start,
        end,
        script_id: inst.scriptId,
        inputs: inst.inputs,
        strategies: st.strategies,
        execution: st.execution,
        warmup_bars: st.warmupBars,
      })
      set({ jobId: id })
      for (;;) {
        await new Promise((r) => setTimeout(r, 350))
        if (get().jobId !== id) return // superseded by a newer run
        const job = await api.backtest(id)
        if (job.status === 'running') continue
        if (job.status === 'error') throw new ApiError(500, job.error ?? 'backtest failed')
        const result = job.result!
        const view = get().view
        set({
          result,
          running: false,
          selectedTradeId: null,
          view: view === 'combined' || result.strategies.some((s) => s.id === view) ? view : 'combined',
        })
        return
      }
    } catch (e) {
      set({ running: false, error: e instanceof Error ? e.message : String(e) })
    }
  },

  /** Show the most recent completed backtest after a page reload (results are stored by the backend).
   *  Skips runs of scripts that no longer exist (e.g. the removed scalping engine): their stored
   *  results name no strategy the tester can show. */
  restoreLatest: async () => {
    if (get().result || get().running) return
    try {
      const known = new Set(useApp.getState().scripts.map((s) => s.id))
      const jobs = await api.backtests()
      const last = jobs.find((j) => j.status === 'done' && known.has(j.request.script_id))
      if (!last) return
      const job = await api.backtest(last.id)
      if (job.result?.strategies.length && !get().result && !get().running) set({ result: job.result, jobId: job.id })
    } catch {
      /* nothing to restore */
    }
  },

  selectTrade: (t) => {
    set({ selectedTradeId: t ? t.id : null })
    if (!t) return
    const res = get().result
    const app = useApp.getState()
    if (res) {
      // the chart must show the backtest's feed and timeframe for the levels to line up
      if (app.feedId !== res.request.feed_id) app.setFeed(res.request.feed_id)
      if (app.timeframe !== res.request.timeframe) app.setTimeframe(res.request.timeframe)
    }
    const end = t.exit_time ?? t.entry_time
    app.focusRange(Math.min(t.signal_time, t.entry_time), end)
  },
}))
