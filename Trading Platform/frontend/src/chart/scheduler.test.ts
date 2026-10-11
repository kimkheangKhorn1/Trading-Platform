import { describe, expect, it } from 'vitest'
import { replayCatchUp, type RunContext } from './scheduler'

const ctx = (end: number | null, inputs = { len: 5 }): RunContext => ({ feedId: 'f', tf: 'M5', scriptId: 's', inputs, start: 0, end })

describe('replayCatchUp', () => {
  it('shows a replay result that is behind the cursor', () => {
    expect(replayCatchUp(ctx(100), ctx(400))).toBe(true)
  })
  it('never shows one ahead of the cursor (after a step back)', () => {
    expect(replayCatchUp(ctx(400), ctx(100))).toBe(false)
  })
  it('never shows one computed with other settings, or a replay result once replay ended', () => {
    expect(replayCatchUp(ctx(100, { len: 9 }), ctx(400))).toBe(false)
    expect(replayCatchUp(ctx(100), ctx(null))).toBe(false)
  })
})
