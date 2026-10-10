import { api } from '../api/client'
import type { InputValue, TimeframeCode } from '../api/types'
import { useIndicators } from '../state/indicators'

export interface RunContext {
  feedId: string
  tf: TimeframeCode
  scriptId: string
  inputs: Record<string, InputValue>
  start: number
  end: number | null // replay cursor; null = up to the latest (live) candle
}

/**
 * Indicator requests per chart instance: debounced, at most one in flight, latest request wins.
 * The backend keeps an incremental runner per context, so repeated calls only compute new bars.
 */
export class IndicatorScheduler {
  private inFlight = new Set<string>()
  private pending = new Map<string, RunContext>()
  private timers = new Map<string, ReturnType<typeof setTimeout>>()
  private lastKey = new Map<string, string>()

  request(uid: string, ctx: RunContext, delayMs = 0, force = false): void {
    const key = JSON.stringify(ctx)
    if (!force && this.lastKey.get(uid) === key && !this.pending.has(uid)) return
    this.lastKey.set(uid, key)
    const t = this.timers.get(uid)
    if (t) clearTimeout(t)
    this.timers.set(uid, setTimeout(() => this.fire(uid, ctx), delayMs))
  }

  forget(uid: string): void {
    const t = this.timers.get(uid)
    if (t) clearTimeout(t)
    this.timers.delete(uid)
    this.pending.delete(uid)
    this.lastKey.delete(uid)
  }

  private async fire(uid: string, ctx: RunContext): Promise<void> {
    if (this.inFlight.has(uid)) {
      this.pending.set(uid, ctx)
      return
    }
    this.inFlight.add(uid)
    const store = useIndicators.getState()
    store.setOutput(uid, { loading: true })
    try {
      const data = await api.runIndicator({
        feed_id: ctx.feedId, timeframe: ctx.tf, script_id: ctx.scriptId, inputs: ctx.inputs,
        start_time: ctx.start, end_time: ctx.end,
      })
      if (!this.pending.has(uid) && this.lastKey.get(uid) === JSON.stringify(ctx)) {
        useIndicators.getState().setOutput(uid, { data, loading: false, error: null })
      }
    } catch (e) {
      useIndicators.getState().setOutput(uid, { loading: false, error: e instanceof Error ? e.message : String(e) })
    } finally {
      this.inFlight.delete(uid)
      const next = this.pending.get(uid)
      if (next) {
        this.pending.delete(uid)
        void this.fire(uid, next)
      }
    }
  }
}
