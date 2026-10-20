import { create } from 'zustand'

export type ReplayMode = 'off' | 'picking' | 'active'

interface ReplayState {
  mode: ReplayMode
  playing: boolean
  speed: number // bars per second
  /** candles form tick by tick from lower-timeframe data (TradingView); off = whole bars */
  smooth: boolean
  cursorTime: number | null // open time of the last visible bar while replaying
  cutoffTime: number | null // absolute completed-data boundary, preserved when changing timeframe
  /** open time of the bar the replay was started from; indicators record their trades from there (null = off) */
  startTime: number | null
  set: (patch: Partial<ReplayState>) => void
}

export const useReplay = create<ReplayState>((set) => ({
  mode: 'off',
  playing: false,
  speed: 2,
  smooth: true,
  cursorTime: null,
  cutoffTime: null,
  startTime: null,
  set: (patch) => set(patch),
}))
