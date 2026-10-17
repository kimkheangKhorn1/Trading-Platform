import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LiveSocket, nonStreamingStatus } from './live'

class Socket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  static instances: Socket[] = []
  readyState = Socket.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  send = vi.fn<(payload: string) => void>()
  close = vi.fn(() => { this.readyState = Socket.CLOSED; this.onclose?.() })
  readonly url: string
  constructor(url: string) { this.url = url; Socket.instances.push(this) }
  open() { this.readyState = Socket.OPEN; this.onopen?.() }
  receive(data: object) { this.onmessage?.({ data: JSON.stringify(data) }) }
  fail() { this.readyState = Socket.CLOSED; this.onclose?.() }
}

beforeEach(() => {
  vi.useFakeTimers()
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' })
})
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('historical feeds and socket lifecycle', () => {
  it('classifies archive/CSV as historical and disconnected broker APIs as offline', () => {
    expect(nonStreamingStatus({ provider: 'exness_archive', live: false })?.state).toBe('static')
    expect(nonStreamingStatus({ provider: 'csv', live: false })?.state).toBe('static')
    expect(nonStreamingStatus({ provider: 'exness', live: false })?.state).toBe('offline')
    expect(nonStreamingStatus({ provider: 'exness', live: true })).toBeNull()
  })

  it('cancels development mount cleanup before starting a handshake', () => {
    const client = new LiveSocket()
    client.connect()
    client.close()
    vi.runOnlyPendingTimers()
    expect(Socket.instances).toHaveLength(0)
  })

  it('queues a subscription and opens only one socket on repeated connect calls', () => {
    const client = new LiveSocket()
    client.subscribe('gold', 'M1')
    client.connect()
    client.connect()
    vi.advanceTimersByTime(0)
    expect(Socket.instances).toHaveLength(1)
    const ws = Socket.instances[0]
    expect(ws.url).toBe('ws://localhost:5173/ws/live')
    ws.open()
    expect(JSON.parse(ws.send.mock.calls[0][0])).toEqual({ action: 'subscribe', feed_id: 'gold', timeframe: 'M1' })
    client.close()
  })

  it('ignores delayed messages from an earlier feed/timeframe subscription', () => {
    const client = new LiveSocket()
    client.onStatus = vi.fn()
    client.subscribe('old', 'M1')
    client.connect()
    vi.advanceTimersByTime(0)
    const ws = Socket.instances[0]
    ws.open()
    client.subscribe('gold', 'M5')
    ws.receive({ type: 'status', feed: 'old', tf: 'M1', state: 'connecting' })
    ws.receive({ type: 'status', feed: 'gold', tf: 'M1', state: 'connecting' })
    expect(client.onStatus).not.toHaveBeenCalled()
    ws.receive({ type: 'status', feed: 'gold', tf: 'M5', state: 'live', message: 'ready' })
    expect(client.onStatus).toHaveBeenCalledExactlyOnceWith('live', 'ready')
    client.close()
  })

  it('disposes pending handshakes after opening without starting pings or retries', () => {
    const client = new LiveSocket()
    client.onStatus = vi.fn()
    client.connect()
    vi.advanceTimersByTime(0)
    const ws = Socket.instances[0]
    client.close()
    expect(ws.close).not.toHaveBeenCalled()
    ws.open()
    expect(ws.close).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(60_000)
    expect(ws.send).not.toHaveBeenCalled()
    expect(client.onStatus).not.toHaveBeenCalled()
    expect(Socket.instances).toHaveLength(1)
  })

  it('reconnects once after failure and ignores a stale socket close callback', () => {
    const client = new LiveSocket()
    client.onStatus = vi.fn()
    client.connect()
    vi.advanceTimersByTime(0)
    const old = Socket.instances[0]
    const staleClose = old.onclose!
    old.fail()
    expect(client.onStatus).toHaveBeenCalledWith('disconnected', expect.stringContaining('1s'))
    vi.advanceTimersByTime(1001)
    expect(Socket.instances).toHaveLength(2)
    Socket.instances[1].open()
    staleClose()
    expect(client.onStatus).toHaveBeenCalledTimes(1)
    client.close()
    vi.advanceTimersByTime(60_000)
    expect(Socket.instances).toHaveLength(2)
  })
})
