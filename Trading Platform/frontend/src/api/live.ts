import type { BarRow, Feed, LiveState, TimeframeCode } from './types'

/** Stored feeds don't need a live-stream connection to display their candles. */
export function nonStreamingStatus(feed: Pick<Feed, 'provider' | 'live'>): { state: LiveState; message: string } | null {
  if (feed.provider === 'exness_archive') return { state: 'static', message: 'Exness archive history — updated by the history pipeline' }
  if (feed.provider === 'csv') return { state: 'static', message: 'Imported data — no live updates' }
  if (!feed.live) return { state: 'offline', message: `${feed.provider === 'exness' ? 'Exness' : 'OANDA'} live API is not connected. Open Data for connection details.` }
  return null
}

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
    if (this.ws || this.timer) return
    // React development setup/cleanup can happen in the same turn. Wait until
    // that turn finishes so cleanup never aborts a just-created handshake.
    this.timer = setTimeout(() => {
      this.timer = null
      if (!this.closed) this.open()
    }, 0)
  }

  private open(): void {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/live`)
    this.ws = ws
    ws.onopen = () => {
      if (this.closed || this.ws !== ws) return
      this.retry = 0
      if (this.sub) this.send({ action: 'subscribe', feed_id: this.sub.feedId, timeframe: this.sub.tf })
      this.pingTimer = setInterval(() => this.send({ action: 'ping' }), 20_000)
    }
    ws.onmessage = (ev) => {
      if (this.closed || this.ws !== ws) return
      let msg: { type: string; [k: string]: unknown }
      try {
        msg = JSON.parse(ev.data as string)
      } catch {
        return
      }
      if ((msg.type === 'status' || msg.type === 'bars') && this.sub
          && (msg.feed !== this.sub.feedId || msg.tf !== this.sub.tf)) return
      if (msg.type === 'bars') this.onBars(msg.feed as string, msg.tf as string, msg.bars as BarRow[])
      else if (msg.type === 'status') this.onStatus(msg.state as LiveState, (msg.message as string) ?? '')
    }
    ws.onclose = () => {
      if (this.ws !== ws) return
      if (this.pingTimer) clearInterval(this.pingTimer)
      this.pingTimer = null
      this.ws = null
      if (this.closed) return
      const delay = Math.min(30_000, 1000 * 2 ** this.retry++)
      this.onStatus('disconnected', `Connection to backend lost - retrying in ${Math.round(delay / 1000)}s`)
      this.timer = setTimeout(() => { this.timer = null; this.connect() }, delay)
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
    this.timer = null
    this.pingTimer = null
    const ws = this.ws
    this.ws = null
    if (ws) {
      ws.onmessage = null
      ws.onclose = null
      // Closing CONNECTING sockets produces Chrome's "closed before ...
      // established" warning. Dispose them when their handshake completes.
      if (ws.readyState === WebSocket.CONNECTING) ws.onopen = () => ws.close()
      else { ws.onopen = null; ws.close() }
    }
  }

  private send(msg: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg))
  }
}
