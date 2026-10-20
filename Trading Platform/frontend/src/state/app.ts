import { create } from 'zustand'
import { api, ApiError } from '../api/client'
import type { Feed, Health, LiveState, ScriptMeta, TimeframeCode } from '../api/types'
import type { TimezoneId } from '../lib/format'
import { load, save } from '../lib/storage'

export const TF_LABELS: Record<TimeframeCode, string> = {
  M1: '1m', M5: '5m', M15: '15m', M30: '30m', H1: '1H', H4: '4H', D: '1D',
}
export const TF_SECONDS: Record<TimeframeCode, number> = {
  M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14_400, D: 86_400,
}
export const ALL_TFS: TimeframeCode[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D']

export type Dialog =
  | null
  | { type: 'indicators' }
  | { type: 'data' }
  | { type: 'settings'; uid: string }
  | { type: 'tuning'; uid: string }
  | { type: 'symbol' }
  | { type: 'goto' }

interface Prefs {
  feedId: string | null
  timeframe: TimeframeCode
  timezone: TimezoneId
}

interface AppState extends Prefs {
  health: Health | null
  feeds: Feed[]
  scripts: ScriptMeta[]
  backendError: string | null
  liveState: LiveState
  liveMessage: string
  dialog: Dialog
  /** chart navigation request (trade inspector, go-to-date); nonce re-triggers identical requests */
  focus: { from: number; to: number; nonce: number } | null
  /** shown while the chart is loading or after a data error */
  chartNotice: string | null
  init: () => Promise<void>
  refreshFeeds: () => Promise<void>
  setFeed: (id: string) => void
  setTimeframe: (tf: TimeframeCode) => void
  setTimezone: (tz: TimezoneId) => void
  setLive: (state: LiveState, message: string) => void
  openDialog: (d: Dialog) => void
  focusRange: (from: number, to: number) => void
  setChartNotice: (msg: string | null) => void
}

const PREFS_KEY = 'tp.prefs.v1'
const prefs = load<Prefs>(PREFS_KEY, { feedId: null, timeframe: 'M15', timezone: 'UTC' })

function persist(s: Prefs) {
  save(PREFS_KEY, { feedId: s.feedId, timeframe: s.timeframe, timezone: s.timezone })
}

export const useApp = create<AppState>((set, get) => ({
  ...prefs,
  health: null,
  feeds: [],
  scripts: [],
  backendError: null,
  liveState: 'connecting',
  liveMessage: '',
  dialog: null,
  focus: null,
  chartNotice: null,

  init: async () => {
    try {
      const [health, feeds, scripts] = await Promise.all([api.health(), api.feeds(), api.scripts()])
      set({ health, feeds, scripts, backendError: null })
      const { feedId } = get()
      const preferred = feeds.find((f) => f.provider === health.market_data_provider && f.live)
        ?? (health.market_data_provider === 'exness'
          ? feeds.find((f) => f.provider === 'exness_archive' && f.price_type === 'bid') : undefined)
      if (!feeds.find((f) => f.id === feedId) || preferred && feeds.find((f) => f.id === feedId)?.provider !== preferred.provider) {
        const pick = preferred ?? feeds[0]
        set({ feedId: pick ? pick.id : null })
      }
      get().setTimeframe(get().timeframe) // re-validate against the feed's available timeframes
    } catch (e) {
      set({ backendError: e instanceof ApiError ? e.message : String(e) })
    }
  },

  refreshFeeds: async () => {
    const feeds = await api.feeds()
    set({ feeds })
    if (!feeds.find((f) => f.id === get().feedId)) set({ feedId: feeds[0]?.id ?? null })
  },

  setFeed: (id) => {
    set({ feedId: id })
    const feed = get().feeds.find((f) => f.id === id)
    if (feed && !feed.timeframes.includes(get().timeframe)) {
      set({ timeframe: feed.timeframes.includes('M15') ? 'M15' : feed.timeframes[0] })
    }
    persist(get())
  },

  setTimeframe: (tf) => {
    const feed = get().feeds.find((f) => f.id === get().feedId)
    if (feed && !feed.timeframes.includes(tf)) tf = feed.timeframes.includes('M15') ? 'M15' : feed.timeframes[0]
    set({ timeframe: tf })
    persist(get())
  },

  setTimezone: (tz) => {
    set({ timezone: tz })
    persist(get())
  },

  setLive: (liveState, liveMessage) => set({ liveState, liveMessage }),
  openDialog: (dialog) => set({ dialog }),
  focusRange: (from, to) => set({ focus: { from, to, nonce: Date.now() } }),
  setChartNotice: (chartNotice) => set({ chartNotice }),
}))

export function currentFeed(): Feed | undefined {
  const s = useApp.getState()
  return s.feeds.find((f) => f.id === s.feedId)
}
