import type { BarRow } from '../api/types'
import type { BadgeOverlapRequest, OverlapReactionResult } from '../api/badgeOverlap'

export interface ReactionJob<B = BadgeOverlapRequest> { uid: string; body: B }
export interface ReactionRequest<B = BadgeOverlapRequest> { key: string; scope: string; jobs: ReactionJob<B>[] }

/** Detect the close itself, including a completion flag or corrected OHLC at the same clock. */
export function confirmedCandleKey(bars:BarRow[], asOf:number|null, step:number):string {
  if (asOf!==null) for (let i=bars.length-1;i>=0;i--)
    if (bars[i][6]===1 && bars[i][0]+step<=asOf) return JSON.stringify(bars[i])
  return ''
}

/** No timer/debounce and no dependency on the source-scan queue. Coalesce only newer reaction work. */
export class ReactionQueue<B = BadgeOverlapRequest, R = OverlapReactionResult> {
  private running = false
  private pending: ReactionRequest<B> | null = null
  private fetch: (body: B) => Promise<R>
  private done: (request: ReactionRequest<B>, results: Record<string, R>, error: string | null) => void
  constructor(fetch: (body: B) => Promise<R>,
    done: (request: ReactionRequest<B>, results: Record<string, R>, error: string | null) => void) {
    this.fetch=fetch
    this.done=done
  }

  request(request: ReactionRequest<B>): void {
    this.pending = request
    if (this.running) return
    this.running = true
    void this.drain()
  }
  private async drain(): Promise<void> {
    try {
      while (this.pending) {
        const request = this.pending
        this.pending = null
        try {
          const entries = await Promise.all(request.jobs.map(async j => [j.uid, await this.fetch(j.body)] as const))
          this.done(request, Object.fromEntries(entries), null)
        } catch (e) {
          this.done(request, {}, e instanceof Error ? e.message : String(e))
        }
      }
    } finally { this.running = false }
  }
}
