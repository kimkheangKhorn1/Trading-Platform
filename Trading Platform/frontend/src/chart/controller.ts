import {
  CandlestickSeries,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle as LwLineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from 'lightweight-charts'
import { api } from '../api/client'
import type { BarRow, IndicatorOutput, ScriptMeta, TimeframeCode } from '../api/types'
import { cssColor, THEME } from '../lib/colors'
import { fmtCrosshair, fmtTick, tradingDate, type TimezoneId } from '../lib/format'
import { TF_SECONDS } from '../state/app'
import type { IndicatorInstance } from '../state/indicators'
import { OverlayPrimitive, type HitInfo, type OverlayLayer, type TradeOverlay } from './overlay'
import { indexAtOrBefore } from './timeIndex'

export interface ControllerEvents {
  onCrosshair: (index: number | null) => void
  onHover: (info: HitInfo | null, x: number, y: number) => void
  onClick: (info: HitInfo | null, time: number | null) => void
  onData: () => void
  onNotice: (msg: string | null) => void
}

const PAGE = 3000
const LW_STYLE = { solid: LwLineStyle.Solid, dashed: LwLineStyle.Dashed, dotted: LwLineStyle.Dotted }

interface CandleStyle { color: string; wickColor: string; borderColor: string }

export class ChartController {
  readonly chart: IChartApi
  readonly candles: ISeriesApi<'Candlestick'>
  readonly overlay = new OverlayPrimitive()
  private plotSeries = new Map<string, ISeriesApi<'Line'>>()
  private paneOf = new Map<string, number>()
  /** Indicator barcolor/plotcandle colors by bar time; sources = the outputs they were built from. */
  private candleStyles = new Map<number, CandleStyle>()
  private candleSources: IndicatorOutput[] = []
  bars: BarRow[] = []
  feedId: string | null = null
  tf: TimeframeCode = 'M15'
  precision = 3
  hasMore = false
  version = 0
  replayIndex: number | null = null
  private loadingMore: Promise<void> | null = null
  private loadSeq = 0
  private tz: TimezoneId = 'UTC'
  private disposed = false
  private ev: ControllerEvents

  constructor(container: HTMLElement, ev: ControllerEvents) {
    this.ev = ev
    this.chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { color: THEME.bg },
        textColor: THEME.text,
        fontSize: 12,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif',
        panes: { separatorColor: THEME.border, separatorHoverColor: 'rgba(41,98,255,0.3)' },
        attributionLogo: true,
      },
      grid: { vertLines: { color: THEME.grid }, horzLines: { color: THEME.grid } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: THEME.crosshair, labelBackgroundColor: '#363a45' },
        horzLine: { color: THEME.crosshair, labelBackgroundColor: '#363a45' },
      },
      rightPriceScale: { borderColor: THEME.border, scaleMargins: { top: 0.08, bottom: 0.12 } },
      timeScale: { borderColor: THEME.border, rightOffset: 8, barSpacing: 7, timeVisible: true, secondsVisible: false },
      kineticScroll: { mouse: false, touch: true },
    })
    this.candles = this.chart.addSeries(CandlestickSeries, {
      upColor: THEME.up,
      downColor: THEME.down,
      borderUpColor: THEME.up,
      borderDownColor: THEME.down,
      wickUpColor: THEME.up,
      wickDownColor: THEME.down,
      priceLineColor: THEME.muted,
    })
    this.candles.attachPrimitive(this.overlay)
    this.applyFormatters()

    this.chart.subscribeCrosshairMove((p) => {
      const idx = p.logical !== undefined && p.logical !== null ? Math.round(p.logical) : null
      this.ev.onCrosshair(idx)
      if (p.point) this.ev.onHover(this.overlay.hit(p.point.x, p.point.y), p.point.x, p.point.y)
      else this.ev.onHover(null, 0, 0)
    })
    this.chart.subscribeClick((p) => {
      const info = p.point ? this.overlay.hit(p.point.x, p.point.y) : null
      const time = typeof p.time === 'number' ? (p.time as number) : null
      this.ev.onClick(info, time)
    })
    this.chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (r && r.from < 30 && this.hasMore && !this.loadingMore) void this.loadMore()
    })
  }

  dispose(): void {
    this.disposed = true
    this.chart.remove()
  }

  // --------------------------------------------------------------- formatting
  setTimezone(tz: TimezoneId): void {
    this.tz = tz
    this.applyFormatters()
  }

  private applyFormatters(): void {
    const daily = this.tf === 'D'
    this.chart.applyOptions({
      localization: {
        timeFormatter: (t: Time) => fmtCrosshair(daily ? tradingDate(t as number) : (t as number), daily ? 'UTC' : this.tz, !daily),
      },
      timeScale: {
        tickMarkFormatter: (t: Time, type: number) =>
          fmtTick(daily ? tradingDate(t as number) : (t as number), daily ? 'UTC' : this.tz, type),
      },
    })
  }

  // --------------------------------------------------------------------- data
  get shownBars(): BarRow[] {
    return this.replayIndex === null ? this.bars : this.bars.slice(0, this.replayIndex + 1)
  }

  get times(): number[] {
    return this.shownBars.map((b) => b[0])
  }

  async load(feedId: string, tf: TimeframeCode): Promise<void> {
    const seq = ++this.loadSeq
    this.feedId = feedId
    this.tf = tf
    this.replayIndex = null
    this.bars = []
    this.hasMore = false
    this.loadingMore = null
    this.applyFormatters()
    this.render()
    this.ev.onNotice('Loading candles…')
    try {
      const res = await api.candles(feedId, tf, { limit: PAGE })
      if (seq !== this.loadSeq || this.disposed) return
      this.bars = res.bars
      this.hasMore = res.has_more
      this.precision = res.precision
      this.candles.applyOptions({ priceFormat: { type: 'price', precision: res.precision, minMove: 10 ** -res.precision } })
      this.render()
      const n = this.bars.length
      this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 8 })
      this.ev.onNotice(res.warning ?? (n === 0 ? 'No candles stored for this timeframe yet.' : null))
    } catch (e) {
      if (seq === this.loadSeq) this.ev.onNotice(e instanceof Error ? e.message : String(e))
    }
  }

  /** Page older history in (called when scrolling near the left edge, or to reach a date). */
  loadMore(): Promise<void> {
    if (this.loadingMore) return this.loadingMore
    if (!this.hasMore || !this.feedId || this.bars.length === 0) return Promise.resolve()
    const seq = this.loadSeq
    const feedId = this.feedId
    const tf = this.tf
    const before = this.bars[0][0]
    this.loadingMore = (async () => {
      try {
        const res = await api.candles(feedId, tf, { before, limit: PAGE })
        if (seq !== this.loadSeq || this.disposed) return
        const older = res.bars.filter((b) => b[0] < before)
        this.hasMore = res.has_more && older.length > 0
        if (older.length === 0) return
        this.bars = [...older, ...this.bars]
        if (this.replayIndex !== null) this.replayIndex += older.length
        // Lightweight Charts anchors the view to the right edge, so prepending keeps the same bars on screen
        this.render()
      } catch (e) {
        this.ev.onNotice(e instanceof Error ? e.message : String(e))
      } finally {
        this.loadingMore = null
      }
    })()
    return this.loadingMore
  }

  /** Merge live candles; returns true if a bar was completed or added (indicators should refresh). */
  applyLive(rows: BarRow[]): boolean {
    if (!rows.length || !this.bars.length) return false
    let structural = false
    let needsFull = false
    for (const row of rows) {
      const n = this.bars.length
      const last = this.bars[n - 1]
      if (row[0] === last[0]) {
        if (row[6] === 1 && last[6] === 0) structural = true
        this.bars[n - 1] = row
      } else if (row[0] > last[0]) {
        this.bars.push(row)
        structural = true
      } else {
        const i = indexAtOrBefore(this.bars.map((b) => b[0]), row[0])
        if (i >= 0 && this.bars[i][0] === row[0]) {
          this.bars[i] = row
          needsFull = true
        }
      }
    }
    if (this.replayIndex !== null) return false // replay hides live data
    if (needsFull) this.render()
    else {
      for (const row of rows.filter((r) => r[0] >= this.bars[this.bars.length - 1][0] - TF_SECONDS[this.tf])) {
        this.candles.update(this.toCandle(row))
      }
      this.syncOverlayBars()
      this.version++
      this.ev.onData()
    }
    return structural
  }

  private render(): void {
    const bars = this.shownBars
    this.candles.setData(bars.map((b) => this.toCandle(b)))
    this.syncOverlayBars()
    this.version++
    this.ev.onData()
  }

  private syncOverlayBars(): void {
    const bars = this.shownBars
    this.overlay.times = bars.map((b) => b[0])
    this.overlay.highs = bars.map((b) => b[2])
    this.overlay.lows = bars.map((b) => b[3])
    this.overlay.tfSeconds = TF_SECONDS[this.tf]
    this.overlay.precision = this.precision
    this.overlay.update()
  }

  // --------------------------------------------------------------- indicators
  setIndicators(instances: IndicatorInstance[], outputs: Record<string, IndicatorOutput | null>, scripts: ScriptMeta[]): void {
    const wanted = new Set<string>()
    const layers: OverlayLayer[] = []
    const shown = new Set(this.times)
    const candleSources: IndicatorOutput[] = []
    let nextPane = 1
    for (const inst of instances) {
      const meta = scripts.find((s) => s.id === inst.scriptId)
      const out = outputs[inst.uid]
      if (!meta || !out || !inst.visible || !inst.timeframes.includes(this.tf)) continue
      if (out.bar_colors.length || out.candle_colors?.length) candleSources.push(out)
      const colorsByPlot = new Map<string, Map<number, string>>()
      for (const [pid, list] of Object.entries(out.plot_colors)) colorsByPlot.set(pid, new Map(list))
      let pane = 0
      if (meta.plots.some((p) => p.pane === 'separate')) pane = nextPane++
      for (const p of meta.plots) {
        const style = inst.plotStyles[p.id]
        if (!style?.visible) continue
        const values = out.plots[p.id]
        if (!values || values.every((v) => v === null)) continue
        const key = `${inst.uid}:${p.id}`
        wanted.add(key)
        const targetPane = p.pane === 'separate' ? pane : 0
        let s = this.plotSeries.get(key)
        if (s && this.paneOf.get(key) !== targetPane) {
          this.chart.removeSeries(s)
          s = undefined
        }
        if (!s) {
          s = this.chart.addSeries(LineSeries, { lastValueVisible: false, priceLineVisible: false,
            crosshairMarkerVisible: false }, targetPane)
          this.plotSeries.set(key, s)
          this.paneOf.set(key, targetPane)
          // Draw overlay plots (SMA/ATR envelopes) before candle bodies, borders and wicks.
          if (targetPane === 0) s.setSeriesOrder(this.candles.seriesOrder())
        }
        s.applyOptions({
          // The first series on a price scale supplies its formatter; preserve candle precision.
          ...(targetPane === 0 ? { priceFormat: this.candles.options().priceFormat } : {}),
          color: cssColor(style.color),
          lineWidth: style.linewidth as 1 | 2 | 3 | 4,
          lineStyle: LW_STYLE[style.linestyle] ?? LwLineStyle.Solid,
          title: '',
        })
        const colors = colorsByPlot.get(p.id)
        const data: (LineData | WhitespaceData)[] = []
        out.times.forEach((t, i) => {
          if (!shown.has(t)) return
          const v = values[i]
          if (v === null || v === undefined) data.push({ time: t as UTCTimestamp })
          else {
            const c = colors?.get(t)
            data.push(c ? { time: t as UTCTimestamp, value: v, color: cssColor(c) } : { time: t as UTCTimestamp, value: v })
          }
        })
        s.setData(data)
      }
      const drawings = out.drawings.filter((d) => !d.group || inst.groupVisible[d.group] !== false)
      const shapes = meta.shapes
        .filter((sp) => inst.shapeStyles[sp.id]?.visible !== false)
        .map((sp) => ({
          id: sp.id, title: sp.title, shape: sp.shape, location: sp.location,
          color: inst.shapeStyles[sp.id]?.color ?? sp.color, text: sp.text, textColor: sp.textcolor, size: sp.size,
          points: out.shapes[sp.id] ?? [],
        }))
      layers.push({ uid: inst.uid, name: meta.short_name, drawings, shapes })
    }
    for (const [key, s] of this.plotSeries) {
      if (!wanted.has(key)) {
        this.chart.removeSeries(s)
        this.plotSeries.delete(key)
        this.paneOf.delete(key)
      }
    }
    this.overlay.layers = layers
    this.overlay.update()
    this.setCandleStyles(candleSources)
  }

  /** Repaint candles only when the coloring outputs changed (setIndicators runs on every data tick). */
  private setCandleStyles(sources: IndicatorOutput[]): void {
    const same = sources.length === this.candleSources.length && sources.every((s, i) => s === this.candleSources[i])
    if (same) return
    this.candleSources = sources
    this.candleStyles = new Map()
    for (const out of sources) {
      for (const [t, c] of out.bar_colors) {
        const css = cssColor(c)
        this.candleStyles.set(t, { color: css, wickColor: css, borderColor: css })
      }
      // plotcandle is drawn over barcolor; a null body is hollow (chart background shows through)
      for (const [t, body, wick, border] of out.candle_colors ?? []) {
        this.candleStyles.set(t, { color: body === null ? THEME.bg : cssColor(body), wickColor: cssColor(wick), borderColor: cssColor(border) })
      }
    }
    this.candles.setData(this.shownBars.map((b) => this.toCandle(b)))
  }

  private toCandle(b: BarRow) {
    const style = this.candleStyles.get(b[0])
    const candle = { time: b[0] as UTCTimestamp, open: b[1], high: b[2], low: b[3], close: b[4] }
    return style ? { ...candle, ...style } : candle
  }

  setTrades(t: TradeOverlay | null): void {
    this.overlay.trades = t
    this.overlay.update()
  }

  // --------------------------------------------------------------- navigation
  async focusRange(from: number, to: number): Promise<void> {
    for (let k = 0; k < 200 && this.bars.length && this.bars[0][0] > from - 40 * TF_SECONDS[this.tf] && this.hasMore; k++) {
      await this.loadMore()
    }
    const times = this.times
    if (!times.length) return
    const a = Math.max(0, indexAtOrBefore(times, from))
    const b = Math.max(a, indexAtOrBefore(times, to))
    const pad = Math.max(25, Math.round((b - a) * 0.6))
    this.chart.timeScale().setVisibleLogicalRange({ from: a - pad, to: b + pad })
  }

  scrollToRealtime(): void {
    this.chart.timeScale().scrollToRealTime()
  }

  // ------------------------------------------------------------------- replay
  startReplay(index: number): void {
    const range = this.chart.timeScale().getVisibleLogicalRange()
    this.replayIndex = Math.max(0, Math.min(index, this.bars.length - 1))
    this.render()
    // keep the picked bar where it was; upcoming replay bars fill the empty space to its right
    if (range) this.chart.timeScale().setVisibleLogicalRange(range)
  }

  stepReplay(): boolean {
    if (this.replayIndex === null || this.replayIndex >= this.bars.length - 1) return false
    this.replayIndex++
    const row = this.bars[this.replayIndex]
    this.candles.update(this.toCandle(row))
    this.syncOverlayBars()
    this.version++
    this.ev.onData()
    return true
  }

  stopReplay(): void {
    this.replayIndex = null
    this.render()
    this.chart.timeScale().scrollToRealTime()
  }
}
