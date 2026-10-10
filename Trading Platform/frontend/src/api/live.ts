import type { BarRow, LiveState, TimeframeCode } from './types'

type BarsListener = (feedId: string, tf: string, bars: BarRow[]) => void
type StatusListener = (state: LiveState, message: string) => void

/** WebSocket client for live candles with automatic reconnection (exponential backoff). */
export class LiveSocket {
  private ws: WebSocket | null = null
  private sub: { feedId: string; tf: TimeframeCode } | null = null
  private retry = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private closed = false
  onBars: BarsListener = () => {}
  onStatus: StatusListener = () => {}

  connect(): void {
    this.closed = false
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/live`)
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      if (this.sub) this.send({ action: 'subscribe', feed_id: this.sub.feedId, timeframe: this.sub.tf })
      this.pingTimer = setInterval(() => this.send({ action: 'ping' }), 20_000)
    }
    ws.onmessage = (ev) => {
      let msg: { type: string; [k: string]: unknown }
      try {
        msg = JSON.parse(ev.data as string)
      } catch {
        return
      }
      if (msg.type === 'bars') this.onBars(msg.feed as string, msg.tf as string, msg.bars as BarRow[])
      else if (msg.type === 'status') this.onStatus(msg.state as LiveState, (msg.message as string) ?? '')
    }
    ws.onclose = () => {
      if (this.pingTimer) clearInterval(this.pingTimer)
      this.ws = null
      if (this.closed) return
      const delay = Math.min(30_000, 1000 * 2 ** this.retry++)
      this.onStatus('disconnected', `Connection to backend lost - retrying in ${Math.round(delay / 1000)}s`)
      this.timer = setTimeout(() => this.connect(), delay)
    }
  }

  subscribe(feedId: string, tf: TimeframeCode): void {
    this.sub = { feedId, tf }
    this.send({ action: 'subscribe', feed_id: feedId, timeframe: tf })
  }

  close(): void {
    this.closed = true
    if (this.timer) clearTimeout(this.timer)
    if (this.pingTimer) clearInterval(this.pingTimer)
    this.ws?.close()
  }

  private send(msg: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg))
  }
}
