import {
  CandlestickSeries,
  createChart,
  CrosshairMode,
  PriceScaleMode,
  LineSeries,
  LineStyle as LwLineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type PriceFormat,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from 'lightweight-charts'
import { api } from '../api/client'
import type { BarRow, IndicatorOutput, ScriptMeta, TimeframeCode } from '../api/types'
import { cssColor, THEME } from '../lib/colors'
import { fmtCrosshair, fmtTick, tradingDate, type TimezoneId } from '../lib/format'
import { TF_SECONDS, useApp } from '../state/app'
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
  /** % mode is drawn on a normal scale with %-labels, so candles, plots and drawings keep one shared base (TradingView). */
  private scaleKind: 'normal' | 'log' | 'percent' = 'normal'
  private pctBase: number | null = null
  hasMore = false
  version = 0
  replayIndex: number | null = null
  /** Replay candle being built from lower-timeframe data (TradingView-style movement); ticks[k] is shown. */
  private forming: { ticks: BarRow[]; k: number } | null = null
  /** Lower-timeframe candles for replay movement, covering [from, to). */
  private sub: { feedId: string; tf: TimeframeCode; chartTf: TimeframeCode; from: number; to: number; rows: BarRow[] } | null = null
  private subLoading: Promise<void> | null = null
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
      if (this.scaleKind === 'percent') this.updatePctBase()
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
    if (this.replayIndex === null) return this.bars
    const shown = this.bars.slice(0, this.replayIndex + 1)
    if (this.forming) shown.push(this.forming.ticks[this.forming.k])
    return shown
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
      this.applyPriceFormat()
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
          // TradingView shows each plot's latest value on the price scale in the plot's color
          lastValueVisible: true,
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

  // ------------------------------------------------------------------- replay
  startReplay(index: number): void {
    const range = this.chart.timeScale().getVisibleLogicalRange()
    this.forming = null
    this.replayIndex = Math.max(0, Math.min(index, this.bars.length - 1))
    void this.prefetchSub(this.replayIndex + 1)
    this.render()
    // keep the picked bar where it was; upcoming replay bars fill the empty space to its right
    if (range) this.chart.timeScale().setVisibleLogicalRange(range)
  }

  stepReplay(): boolean {
    if (this.replayIndex === null || this.replayIndex >= this.bars.length - 1) return false
    this.forming = null // the full bar replaces any partly formed one (same time)
    this.replayIndex++
    const row = this.bars[this.replayIndex]
    this.candles.update(this.toCandle(row))
    this.syncOverlayBars()
    this.version++
    this.ev.onData()
    return true
  }

  /** One bar back: candles can only be appended in place, so the shown range is redrawn. */
  stepBackReplay(): boolean {
    if (this.replayIndex === null) return false
    const range = this.chart.timeScale().getVisibleLogicalRange()
    if (this.forming) this.forming = null // back to the last completed bar
    else if (this.replayIndex > 0) this.replayIndex--
    else return false
    this.render()
    if (range) this.chart.timeScale().setVisibleLogicalRange(range)
    return true
  }

  stopReplay(): void {
    this.forming = null
    this.replayIndex = null
    this.render()
    this.chart.timeScale().scrollToRealTime()
  }

  /** Open time of the last completed replay bar: indicators are computed up to here, never the forming one. */
  replayCursorTime(): number | null {
    return this.replayIndex === null ? null : this.bars[this.replayIndex]?.[0] ?? null
  }

  /** Ticks the current (or next) replay candle forms in; play speed spreads one bar's time over them. */
  replayTickCount(): number {
    return this.forming?.ticks.length ?? 1
  }

  /**
   * Plays the replay like TradingView: the next candle forms from lower-timeframe candles (its open stays,
   * high/low stretch and the close moves) before it completes. maxTicks caps the steps per candle (1 = whole bars).
   * Returns 'tick' for a partial update, 'bar' when a candle completed, 'end' at the last bar.
   */
  advanceReplay(maxTicks: number): 'tick' | 'bar' | 'end' {
    if (this.replayIndex === null || this.replayIndex >= this.bars.length - 1) return 'end'
    if (maxTicks <= 1 && !this.forming) return this.stepReplay() ? 'bar' : 'end'
    if (!this.forming) {
      const next = this.replayIndex + 1
      this.forming = { ticks: this.buildTicks(next, maxTicks), k: -1 }
      if (!this.subCovers(next + 20)) void this.prefetchSub(next + 1)
    }
    this.forming.k++
    // the last tick is the completed bar itself
    if (this.forming.k >= this.forming.ticks.length - 1) return this.stepReplay() ? 'bar' : 'end'
    this.candles.update(this.toCandle(this.forming.ticks[this.forming.k]))
    this.version++
    this.ev.onData()
    return 'tick'
  }

  /** Lower timeframe that drives replay movement for the chart timeframe, if the feed has it. */
  private subTf(): TimeframeCode | null {
    const prefer: Partial<Record<TimeframeCode, TimeframeCode[]>> = {
      M5: ['M1'], M15: ['M1', 'M5'], M30: ['M1', 'M5'], H1: ['M1', 'M5'], H4: ['M5', 'M15', 'M1'], D: ['M30', 'M15', 'H1'],
    }
    const have = useApp.getState().feeds.find((f) => f.id === this.feedId)?.timeframes ?? []
    return (prefer[this.tf] ?? []).find((t) => have.includes(t)) ?? null
  }

  private subCovers(index: number): boolean {
    const bar = this.bars[Math.min(index, this.bars.length - 1)]
    const c = this.sub
    return !!bar && !!c && c.feedId === this.feedId && c.chartTf === this.tf && c.from <= bar[0] && bar[0] + TF_SECONDS[this.tf] <= c.to
  }

  /** Loads lower-timeframe candles for about 100 replay bars starting at bar ``index``. */
  private prefetchSub(index: number): Promise<void> {
    const tf = this.subTf()
    if (!tf || !this.feedId || index >= this.bars.length || this.subLoading) return this.subLoading ?? Promise.resolve()
    const feedId = this.feedId
    const chartTf = this.tf
    const span = TF_SECONDS[chartTf]
    const from = this.bars[index][0]
    const last = Math.min(this.bars.length - 1, index + 99)
    const to = this.bars[last][0] + span
    const limit = Math.min(20000, Math.ceil((to - from) / TF_SECONDS[tf]) + 2)
    this.subLoading = api.candles(feedId, tf, { before: to, limit }).then((res) => {
      if (this.feedId !== feedId || this.tf !== chartTf) return
      const rows = res.bars.filter((r) => r[0] >= from && r[0] < to && r[6] !== 0)
      // a full page may not reach back to ``from``: then coverage starts at its first candle
      const start = res.bars.length >= limit && rows.length ? rows[0][0] : from
      this.sub = { feedId, tf, chartTf, from: start, to, rows }
    }).catch(() => { /* movement falls back to the candle's own OHLC path */ }).finally(() => { this.subLoading = null })
    return this.subLoading
  }

  /** Partial candles for bar ``index``, ending with the completed bar. */
  private buildTicks(index: number, maxTicks: number): BarRow[] {
    const bar = this.bars[index]
    const [t, o, h, l, c, v] = bar
    const end = t + TF_SECONDS[this.tf]
    const subs = this.subCovers(index) ? this.sub!.rows.filter((r) => r[0] >= t && r[0] < end) : []
    // price path inside the candle: each lower-timeframe candle as open -> nearer extreme -> other extreme -> close
    const path: { price: number; hi: number; lo: number; vol: number }[] = []
    const walk = (open: number, high: number, low: number, close: number, vol: number) => {
      const first = Math.abs(open - low) < Math.abs(open - high) ? low : high
      const pts = [open, first, first === low ? high : low, close]
      pts.forEach((price, j) => path.push({ price, hi: Math.max(open, ...pts.slice(0, j + 1)), lo: Math.min(open, ...pts.slice(0, j + 1)), vol: vol * (j / 3) }))
    }
    if (subs.length) {
      let done = 0 // volume of the lower-timeframe candles already completed
      for (const r of subs) {
        const before = path.length
        if (subs.length <= 15) walk(r[1], r[2], r[3], r[4], r[5])
        else path.push({ price: r[4], hi: r[2], lo: r[3], vol: r[5] })
        for (let j = before; j < path.length; j++) path[j].vol += done
        done += r[5]
      }
    } else {
      walk(o, h, l, c, v)
      // interpolate the four corners so the move is not a jump
      const corners = path.splice(0)
      for (let j = 0; j < corners.length - 1; j++) {
        for (let s = 0; s < 4; s++) {
          const f = s / 4
          const price = corners[j].price + (corners[j + 1].price - corners[j].price) * f
          path.push({ price, hi: Math.max(corners[j].hi, price), lo: Math.min(corners[j].lo, price), vol: v * ((j * 4 + s) / 12) })
        }
      }
    }
    // running candle after each point, thinned to at most maxTicks steps
    let hi = o
    let lo = o
    const rows: BarRow[] = path.map((p) => {
      hi = Math.max(hi, p.hi)
      lo = Math.min(lo, p.lo)
      return [t, o, Math.min(hi, h), Math.max(lo, l), Math.min(h, Math.max(l, p.price)), p.vol, 0] as BarRow
    })
    const keep = Math.max(1, maxTicks - 1)
    const thinned = rows.length <= keep ? rows : Array.from({ length: keep }, (_, k) => rows[Math.floor(((k + 1) * rows.length) / keep) - 1])
    return [...thinned, bar]
  }

  /** Price scale like TradingView's auto / log / % buttons. */
  setPriceScale(auto: boolean, kind: 'normal' | 'log' | 'percent'): void {
    this.scaleKind = kind
    this.chart.priceScale('right').applyOptions({ autoScale: auto, mode: kind === 'log' ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal })
    this.pctBase = null
    if (kind === 'percent') this.updatePctBase()
    this.applyPriceFormat()
  }

  /** % mode: changes are measured from the close of the first visible bar, as on TradingView. */
  private updatePctBase(): void {
    const r = this.chart.timeScale().getVisibleLogicalRange()
    const bars = this.shownBars
    if (!r || !bars.length) return
    const base = bars[Math.min(bars.length - 1, Math.max(0, Math.ceil(r.from)))][4]
    if (base !== this.pctBase) {
      this.pctBase = base
      this.applyPriceFormat()
    }
  }

  private priceFormat(): PriceFormat {
    const minMove = 10 ** -this.precision
    const base = this.pctBase
    if (this.scaleKind === 'percent' && base) {
      return { type: 'custom', minMove, formatter: (p: number) => {
        const v = (p / base - 1) * 100
        return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
      } }
    }
    return { type: 'price', precision: this.precision, minMove }
  }

  /** The main pane's labels come from its series' price format; keep candles and pane-0 plots in sync. */
  private applyPriceFormat(): void {
    const priceFormat = this.priceFormat()
    this.candles.applyOptions({ priceFormat })
    for (const [key, series] of this.plotSeries) if ((this.paneOf.get(key) ?? 0) === 0) series.applyOptions({ priceFormat })
  }

  /** False once the user has stretched or dragged the price axis (the chart turns auto-scale off itself). */
  isAutoScale(): boolean {
    return this.chart.priceScale('right').options().autoScale
  }

  /** Alt+R: auto-scale prices, default bar spacing, back to the latest bar. */
  resetScale(): void {
    this.chart.priceScale('right').applyOptions({ autoScale: true })
    this.chart.timeScale().resetTimeScale()
    this.chart.timeScale().scrollToRealTime()
  }
}
