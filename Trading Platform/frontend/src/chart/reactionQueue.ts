import type { BarRow } from '../api/types'
import type { BadgeOverlapRequest, OverlapReactionResult } from '../api/badgeOverlap'

export interface ReactionJob { uid: string; body: BadgeOverlapRequest }
export interface ReactionRequest { key: string; scope: string; jobs: ReactionJob[] }

/** Detect the close itself, including a completion flag or corrected OHLC at the same clock. */
export function confirmedCandleKey(bars:BarRow[], asOf:number|null, step:number):string {
  if (asOf!==null) for (let i=bars.length-1;i>=0;i--)
    if (bars[i][6]===1 && bars[i][0]+step<=asOf) return JSON.stringify(bars[i])
  return ''
}

/** No timer/debounce and no dependency on the source-scan queue. Coalesce only newer reaction work. */
export class ReactionQueue {
  private running = false
  private pending: ReactionRequest | null = null
  private fetch: (body: BadgeOverlapRequest) => Promise<OverlapReactionResult>
  private done: (request: ReactionRequest, results: Record<string, OverlapReactionResult>, error: string | null) => void
  constructor(fetch: (body: BadgeOverlapRequest) => Promise<OverlapReactionResult>,
    done: (request: ReactionRequest, results: Record<string, OverlapReactionResult>, error: string | null) => void) {
    this.fetch=fetch
    this.done=done
  }

  request(request: ReactionRequest): void {
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
