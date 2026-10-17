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
  set: (patch: Partial<ReplayState>) => void
}

export const useReplay = create<ReplayState>((set) => ({
  mode: 'off',
  playing: false,
  speed: 2,
  smooth: true,
  cursorTime: null,
  cutoffTime: null,
  set: (patch) => set(patch),
}))
