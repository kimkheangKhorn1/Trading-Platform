import { create } from 'zustand'

export type ReplayMode = 'off' | 'picking' | 'active'

interface ReplayState {
  mode: ReplayMode
  playing: boolean
  speed: number // bars per second
  cursorTime: number | null // open time of the last visible bar while replaying
  set: (patch: Partial<ReplayState>) => void
}

export const useReplay = create<ReplayState>((set) => ({
  mode: 'off',
  playing: false,
  speed: 2,
  cursorTime: null,
  set: (patch) => set(patch),
}))
