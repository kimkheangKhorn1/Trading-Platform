import { create } from 'zustand'

export type PriceScaleKind = 'normal' | 'log' | 'percent'

/** TradingView-style price scale controls shared by the chart and the footer buttons. */
export const useScale = create<{
  auto: boolean
  kind: PriceScaleKind
  /** bumped to ask the chart to reset (Alt+R / reset button) */
  resetSeq: number
  set: (p: Partial<{ auto: boolean; kind: PriceScaleKind }>) => void
  reset: () => void
}>((set) => ({
  auto: true,
  kind: 'normal',
  resetSeq: 0,
  set: (p) => set(p),
  reset: () => set((s) => ({ auto: true, resetSeq: s.resetSeq + 1 })),
}))
