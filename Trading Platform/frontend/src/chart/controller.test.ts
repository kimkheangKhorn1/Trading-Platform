import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BarRow, CandlesResponse } from '../api/types'
import { api } from '../api/client'
import { ChartController } from './controller'

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: {}, LineSeries: {}, CrosshairMode: { Normal: 0 },
  PriceScaleMode: { Normal: 0, Logarithmic: 1 }, LineStyle: { Solid: 0, Dashed: 1, Dotted: 2 },
  createChart: () => {
    const scale = { setVisibleLogicalRange: vi.fn(), subscribeVisibleLogicalRangeChange: vi.fn(),
      getVisibleLogicalRange: () => ({ from: 0, to: 160 }), applyOptions: vi.fn(),
      options: () => ({ barSpacing: 7 }), timeToCoordinate: () => 100, width: () => 1000, scrollToRealTime: vi.fn() }
    return { addSeries: () => ({ attachPrimitive: vi.fn(), setData: vi.fn(), update: vi.fn(), applyOptions: vi.fn() }),
      applyOptions: vi.fn(), subscribeCrosshairMove: vi.fn(), subscribeClick: vi.fn(), timeScale: () => scale,
      remove: vi.fn() }
  },
}))

const row = (time: number): BarRow => [time, 100, 110, 90, 105, 1, 1]
const response = (bars: BarRow[], newer = false): CandlesResponse => ({
  feed_id: 'f', timeframe: 'M1', precision: 3, bars, has_more: false, has_newer: newer, warning: null,
})
const controller = () => new ChartController({} as HTMLElement, {
  onData: vi.fn(), onNotice: vi.fn(), onClick: vi.fn(), onHover: vi.fn(), onCrosshair: vi.fn(),
})
beforeEach(() => vi.restoreAllMocks())

describe('direct historical date navigation', () => {
  it('reaches 2020 in two requests, keeps a bounded window, and explains a date without quotes', async () => {
    const target = 1577836800 // 2020-01-01, no source quotes on this UTC date
    const before = 1577815140 // 2019-12-31 17:59
    const next = 1577926800 // 2020-01-02 01:00
    const calls = vi.spyOn(api, 'candles').mockResolvedValueOnce({ ...response([row(before)]), has_more: true })
      .mockResolvedValueOnce(response([row(next), row(next + 60)], true))
    const notice = vi.fn()
    const c = new ChartController({} as HTMLElement, {
      onData: vi.fn(), onNotice: notice, onClick: vi.fn(), onHover: vi.fn(), onCrosshair: vi.fn(),
    })
    c.feedId = 'f'
    c.tf = 'M1'
    c.bars = [row(1791909000)]
    c.hasMore = true
    await c.focusRange(target,target)
    expect(calls).toHaveBeenCalledTimes(2)
    expect(calls.mock.calls[0][2]).toEqual({ before: target+1, limit: 3000 })
    expect(calls.mock.calls[1][2]).toEqual({ after: target, limit: 3000 })
    expect(c.bars.map(b => b[0])).toEqual([before,next,next+60])
    expect(c.hasNewer).toBe(true)
    expect(c.replayIndex).toBeNull()
    expect(notice).toHaveBeenLastCalledWith(expect.stringContaining('2020-01-02 01:00 UTC'))
    expect(c.chart.timeScale().setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -24, to: 26 })
    // A live tick cannot append today's price to a separated historical window.
    expect(c.applyLive([row(1791909000)])).toBe(false)
    expect(c.bars).toHaveLength(3)
  })

  it('does not move the chart after an older date request loses a race with a timeframe switch', async () => {
    let first!: (r: CandlesResponse) => void
    let second!: (r: CandlesResponse) => void
    vi.spyOn(api,'candles').mockReturnValueOnce(new Promise(resolve => { first=resolve }))
      .mockReturnValueOnce(new Promise(resolve => { second=resolve }))
      .mockResolvedValueOnce(response([row(90000)]))
    const c=controller()
    c.feedId='f';c.tf='M1';c.bars=[row(90000)]
    const focus=c.focusRange(0,0)
    await c.load('f','H1')
    const scale=c.chart.timeScale()
    const count=vi.mocked(scale.setVisibleLogicalRange).mock.calls.length
    first(response([row(0)]));second(response([row(60)]))
    await focus
    expect(c.tf).toBe('H1')
    expect(c.bars[0][0]).toBe(90000)
    expect(scale.setVisibleLogicalRange).toHaveBeenCalledTimes(count)
  })

  it('honors the replay cutoff when a user requests a future date', async () => {
    const calls=vi.spyOn(api,'candles').mockResolvedValueOnce(response([row(0),row(60),row(120)]))
      .mockResolvedValueOnce(response([row(180)]))
    const notice=vi.fn()
    const c=new ChartController({} as HTMLElement,{
      onData:vi.fn(),onNotice:notice,onClick:vi.fn(),onHover:vi.fn(),onCrosshair:vi.fn(),
    })
    await c.load('f','M1',120)
    await c.focusRange(3600,3600)
    expect(calls).toHaveBeenCalledTimes(2)
    expect(c.replayCutoffTime()).toBe(120)
    expect(notice).toHaveBeenLastCalledWith(expect.stringContaining('after the replay cursor'))
  })
})

describe('historical replay loading', () => {
  it('loads around the cutoff, shows only completed bars, and preserves the clock through coarse/fine switching', async () => {
    const candles = vi.spyOn(api, 'candles').mockResolvedValueOnce(response([row(0), row(60), row(120)]))
      .mockResolvedValueOnce(response([row(180), row(240)], true))
      .mockResolvedValueOnce(response([row(0), row(3600), row(7200)]))
      .mockResolvedValueOnce(response([row(10800)]))
    const c = controller()
    await c.load('f', 'M1', 120)
    expect(c.shownBars.map((b) => b[0])).toEqual([0, 60])
    expect(c.replayCutoffTime()).toBe(120)
    expect(candles.mock.calls[0][2]).toMatchObject({ before: 121 })
    expect(candles.mock.calls[1][2]).toMatchObject({ after: 120 })
    await c.load('f', 'H1', 7500)
    expect(c.shownBars.map((b) => b[0])).toEqual([0, 3600])
    expect(c.replayCutoffTime()).toBe(7500)
    expect(c.loading).toBe(false)
  })

  it('keeps the latest timeframe when a superseded candle request resolves late', async () => {
    let oldBefore!: (r: CandlesResponse) => void
    let oldAfter!: (r: CandlesResponse) => void
    vi.spyOn(api, 'candles').mockReturnValueOnce(new Promise((resolve) => { oldBefore = resolve }))
      .mockReturnValueOnce(new Promise((resolve) => { oldAfter = resolve }))
      .mockResolvedValueOnce(response([row(0), row(3600), row(7200)]))
      .mockResolvedValueOnce(response([row(10800)]))
    const c = controller()
    const old = c.load('f', 'M1', 120)
    await c.load('f', 'H1', 7500)
    oldBefore(response([row(0), row(60), row(120)]))
    oldAfter(response([row(180)]))
    expect(await old).toBe(false)
    expect(c.tf).toBe('H1')
    expect(c.shownBars.at(-1)?.[0]).toBe(3600)
    expect(c.replayCutoffTime()).toBe(7500)
  })

  it('pages forward without revealing the fetched future bars or appending live data across a history gap', async () => {
    const candles = vi.spyOn(api, 'candles').mockResolvedValueOnce(response([row(0), row(60), row(120)]))
      .mockResolvedValueOnce(response([row(180), row(240)], true))
      .mockResolvedValueOnce(response([row(300), row(360)]))
    const c = controller()
    await c.load('f', 'M1', 120)
    expect(c.applyLive([row(99999)])).toBe(false)
    expect(c.bars.at(-1)?.[0]).toBe(240)
    expect(c.stepReplay()).toBe(true)
    await Promise.resolve()
    await Promise.resolve()
    expect(candles.mock.calls[2][2]).toMatchObject({ after: 240 })
    expect(c.bars.at(-1)?.[0]).toBe(360)
    expect(c.shownBars.at(-1)?.[0]).toBe(120)
    expect(c.replayCutoffTime()).toBe(180)
  })

  it('exit replay reloads the latest chart when the historical window does not reach the present', async () => {
    vi.spyOn(api, 'candles').mockResolvedValueOnce(response([row(0), row(60), row(120)]))
      .mockResolvedValueOnce(response([row(180), row(240)], true))
      .mockResolvedValueOnce(response([row(90000), row(90060)]))
    const c = controller()
    await c.load('f', 'M1', 120)
    c.stopReplay()
    await Promise.resolve()
    await Promise.resolve()
    expect(c.replayIndex).toBe(null)
    expect(c.replayCutoffTime()).toBe(null)
    expect(c.shownBars.at(-1)?.[0]).toBe(90060)
  })
})
