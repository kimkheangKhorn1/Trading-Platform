import {
  CandlestickSeries,
  createChart,
  CrosshairMode,
  PriceScaleMode,
  LineSeries,
  LineStyle as LwLineStyle,
  createTextWatermark,
  type IChartApi,
  type ITextWatermarkPluginApi,
  type MouseEventParams,
  type ISeriesApi,
  type AutoscaleInfo,
  type LineData,
  type PriceFormat,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from 'lightweight-charts'
import { api } from '../api/client'
import type { BarRow, IndicatorOutput, ScriptMeta, TimeframeCode } from '../api/types'
import { cssColor, THEME } from '../lib/colors'
import { fmtCrosshair, fmtDateTime, fmtTick, tradingDate, type TimezoneId } from '../lib/format'
import { TF_SECONDS, useApp } from '../state/app'
import type { IndicatorInstance } from '../state/indicators'
import { OverlayPrimitive, type HitInfo, type OverlayLayer, type TradeOverlay } from './overlay'
import { indexAtOrBefore } from './timeIndex'
import { barCloseTime, completedReplayIndex } from './replayTime'

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
  /** Hover card of each plot line that has a tooltip. */
  private plotTips = new Map<ISeriesApi<'Line'>, { title: string; body: string; pane: number }>()
  /** Pane titles (PlotSpec.pane_group) by `uid:group`. */
  private paneTitles = new Map<string, { pane: number; text: string; wm: ITextWatermarkPluginApi<Time> }>()
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
  loading = false
  hasNewer = false
  private replayCutoff: number | null = null
  private loadingForward: Promise<void> | null = null
  /** Replay candle being built from lower-timeframe data (TradingView-style movement); ticks[k] is shown. */
  private forming: { ticks: BarRow[]; k: number } | null = null
  /** Lower-timeframe candles for replay movement, covering [from, to). */
  private sub: { feedId: string; tf: TimeframeCode; chartTf: TimeframeCode; from: number; to: number; rows: BarRow[] } | null = null
  private subLoading: Promise<void> | null = null
  private lastIndicatorKey: unknown[] | null = null
  private loadingMore: Promise<void> | null = null
  private loadSeq = 0
  private focusSeq = 0
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
      // While a replay candle forms, fit prices to the completed bars only: re-fitting on every new tick
      // high/low moved all labels a pixel or two and made them hop between layout slots (flicker).
      // The scale catches up once when the candle completes; it can use the scale margins until then.
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const info = original()
        const range = this.forming ? this.chart.timeScale().getVisibleLogicalRange() : null
        if (!info || !range) return info
        const done = this.completedBars
        let lo = Infinity
        let hi = -Infinity
        for (let i = Math.max(0, Math.floor(range.from)); i <= Math.min(done.length - 1, Math.ceil(range.to)); i++) {
          lo = Math.min(lo, done[i][3])
          hi = Math.max(hi, done[i][2])
        }
        return Number.isFinite(lo) ? { ...info, priceRange: { minValue: lo, maxValue: hi } } : info
      },
    })
    this.candles.attachPrimitive(this.overlay)
    this.applyFormatters()

    this.chart.subscribeCrosshairMove((p) => {
      const idx = p.logical !== undefined && p.logical !== null ? Math.round(p.logical) : null
      this.ev.onCrosshair(idx)
      if (p.point) {
        // The point is relative to the hovered pane: drawings live in the price pane only.
        const pane = p.paneIndex ?? 0
        const info = (pane === 0 ? this.overlay.hit(p.point.x, p.point.y) : null) ?? this.plotHit(p, pane)
        this.ev.onHover(info, p.point.x, p.point.y + this.paneTop(pane))
      } else this.ev.onHover(null, 0, 0)
    })
    this.chart.subscribeClick((p) => {
      const info = p.point && (p.paneIndex ?? 0) === 0 ? this.overlay.hit(p.point.x, p.point.y) : null
      // right of the last shown bar (e.g. past the replay cursor) a click means that last bar
      const last = this.shownBars.length - 1
      const time = typeof p.time === 'number' ? (p.time as number)
        : p.logical !== undefined && p.logical > last && last >= 0 ? this.shownBars[last][0] : null
      this.ev.onClick(info, time)
    })
    this.chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (r && r.from < 30 && this.hasMore && !this.loadingMore && !this.loading) void this.loadMore()
      if (r && r.to > this.bars.length - 30 && this.hasNewer && this.replayIndex === null && !this.loading) void this.loadForward()
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

  /** Shown bars without a replay candle that is still forming: what indicators and drawings are laid out on. */
  private get completedBars(): BarRow[] {
    return this.replayIndex === null ? this.bars : this.bars.slice(0, this.replayIndex + 1)
  }

  async load(feedId: string, tf: TimeframeCode, replayCutoff: number | null = null, anchor: number | null = null): Promise<boolean> {
    const seq = ++this.loadSeq
    this.loading = true
    this.feedId = feedId
    this.tf = tf
    this.overlay.focusTime = null
    this.forming = null
    this.sub = null
    this.subLoading = null
    this.replayCutoff = replayCutoff
    this.replayIndex = replayCutoff === null ? null : -1
    this.bars = []
    this.hasMore = false
    this.hasNewer = false
    this.loadingMore = null
    this.loadingForward = null
    this.applyFormatters()
    this.render()
    this.ev.onNotice('Loading candles…')
    try {
      // Fetch around the historical clock directly, rather than loading today's chart then walking back.
      const around = replayCutoff ?? anchor
      const [res, ahead] = around === null
        ? [await api.candles(feedId, tf, { limit: PAGE }), null]
        : await Promise.all([
          api.candles(feedId, tf, { before: around + 1, limit: PAGE }),
          api.candles(feedId, tf, { after: around, limit: PAGE }),
        ])
      if (seq !== this.loadSeq || this.disposed) return false
      this.bars = [...res.bars, ...(ahead?.bars ?? [])]
      this.hasMore = res.has_more
      this.hasNewer = ahead ? ahead.has_newer : res.has_newer
      this.precision = res.precision
      if (replayCutoff !== null) {
        const alignment = useApp.getState().feeds.find((f) => f.id === feedId)?.alignment
        this.replayIndex = completedReplayIndex(this.bars, tf, replayCutoff, alignment)
      }
      this.applyPriceFormat()
      this.render()
      const n = this.shownBars.length
      const at = anchor === null ? n - 80 : Math.max(0, indexAtOrBefore(this.times, anchor))
      this.chart.timeScale().setVisibleLogicalRange(anchor === null
        ? { from: Math.max(0, n - 160), to: n + 8 } : { from: at - 80, to: at + 80 })
      // Installing a new window can emit intermediate range callbacks. Settle
      // its viewport before enabling edge paging or indicator calculations.
      this.loading = false
      this.ev.onNotice(res.warning ?? (n === 0 ? (replayCutoff === null
        ? 'No candles stored for this timeframe yet.' : 'No completed candles before this replay time on this timeframe.') : null))
      if (this.replayIndex !== null) void this.prefetchSub(this.replayIndex + 1)
      return true
    } catch (e) {
      if (seq === this.loadSeq) this.ev.onNotice(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      if (seq === this.loadSeq) {
        this.loading = false
        this.ev.onData()
      }
    }
  }

  /** Page older history in (called when scrolling near the left edge, or to reach a date). */
  loadMore(limit = PAGE): Promise<void> {
    if (this.loadingMore) return this.loadingMore
    if (!this.hasMore || !this.feedId || this.bars.length === 0) return Promise.resolve()
    const seq = this.loadSeq
    const feedId = this.feedId
    const tf = this.tf
    const before = this.bars[0][0]
    this.loadingMore = (async () => {
      try {
        const res = await api.candles(feedId, tf, { before, limit })
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
        if (seq === this.loadSeq) this.loadingMore = null
      }
    })()
    return this.loadingMore
  }

  /** Fetch the next page when historical replay reaches the edge of its loaded window. */
  private loadForward(): Promise<void> {
    if (this.loadingForward) return this.loadingForward
    if (!this.hasNewer || !this.feedId || !this.bars.length) return Promise.resolve()
    const seq = this.loadSeq
    const after = this.bars[this.bars.length - 1][0]
    this.loadingForward = api.candles(this.feedId, this.tf, { after, limit: PAGE }).then((res) => {
      if (seq !== this.loadSeq || this.disposed) return
      const next = res.bars.filter((b) => b[0] > after)
      this.hasNewer = res.has_newer && next.length > 0
      this.bars.push(...next)
      if (this.replayIndex === null) {
        const scale = this.chart.timeScale()
        const range = scale.getVisibleLogicalRange()
        this.render()
        if (range) scale.setVisibleLogicalRange(range)
      } else this.ev.onData()
    }).catch((e) => {
      if (seq === this.loadSeq) this.ev.onNotice(e instanceof Error ? e.message : String(e))
    }).finally(() => { if (seq === this.loadSeq) this.loadingForward = null })
    return this.loadingForward
  }

  /** Merge live candles; returns true if a bar was completed or added (indicators should refresh). */
  applyLive(rows: BarRow[]): boolean {
    if (!rows.length || !this.bars.length) return false
    if (this.hasNewer) return false // don't append today's bar across an unloaded historical gap
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
        } else {
          // Native history reconciliation can fill a bar missed while the tick stream was disconnected.
          this.bars.splice(i + 1, 0, row)
          if (this.replayIndex !== null && i + 1 <= this.replayIndex) this.replayIndex++
          needsFull = true
          structural = true
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
    const bars = this.completedBars
    this.overlay.times = bars.map((b) => b[0])
    this.overlay.highs = bars.map((b) => b[2])
    this.overlay.lows = bars.map((b) => b[3])
    this.overlay.tfSeconds = TF_SECONDS[this.tf]
    this.overlay.precision = this.precision
    this.overlay.update()
  }

  // --------------------------------------------------------------- indicators
  setIndicators(instances: IndicatorInstance[], outputs: Record<string, IndicatorOutput | null>, scripts: ScriptMeta[]): void {
    // Called on every data tick. While a replay candle forms only that candle changes, so rebuilding plots and
    // re-laying out labels each tick would only make drawings jump: skip when nothing they depend on changed.
    const bars = this.completedBars
    const key = [instances, outputs, scripts, this.feedId, this.tf, bars.length, bars[0]?.[0], bars[bars.length - 1]?.[0], bars[bars.length - 1]?.[4]]
    if (this.lastIndicatorKey && key.every((v, i) => v === this.lastIndicatorKey![i])) return
    this.lastIndicatorKey = key
    const wanted = new Set<string>()
    const layers: OverlayLayer[] = []
    const shown = new Set(bars.map((b) => b[0]))
    const candleSources: IndicatorOutput[] = []
    let nextPane = 1
    const tips = new Map<ISeriesApi<'Line'>, { title: string; body: string; pane: number }>()
    const titles = new Map<string, { pane: number; text: string }>()
    for (const inst of instances) {
      const meta = scripts.find((s) => s.id === inst.scriptId)
      const out = outputs[inst.uid]
      if (!meta || !out || !inst.visible || !inst.timeframes.includes(this.tf)) continue
      if (out.bar_colors.length || out.candle_colors?.length) candleSources.push(out)
      const colorsByPlot = new Map<string, Map<number, string>>()
      for (const [pid, list] of Object.entries(out.plot_colors)) colorsByPlot.set(pid, new Map(list))
      // One pane per pane group of this indicator, numbered in plot order, only for groups with something to draw.
      const groupPane = new Map<string, number>()
      for (const p of meta.plots) {
        const style = inst.plotStyles[p.id]
        if (!style?.visible) continue
        const values = out.plots[p.id]
        if (!values || values.every((v) => v === null)) continue
        const key = `${inst.uid}:${p.id}`
        wanted.add(key)
        const group = p.pane_group ?? ''
        if (p.pane === 'separate' && !groupPane.has(group)) groupPane.set(group, nextPane++)
        const targetPane = p.pane === 'separate' ? groupPane.get(group)! : 0
        if (targetPane > 0 && group) titles.set(`${inst.uid}:${group}`, { pane: targetPane, text: group })
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
        if (p.tooltip) tips.set(s, { title: p.title, body: p.tooltip, pane: targetPane })
        // plot.style_circles / style_cross: one dot per value, no connecting line
        const dots = p.style === 'circles' || p.style === 'cross'
        s.applyOptions({
          // The first series on a price scale supplies its formatter; preserve candle precision.
          ...(targetPane === 0 ? { priceFormat: this.candles.options().priceFormat } : {}),
          color: cssColor(style.color),
          lineWidth: style.linewidth as 1 | 2 | 3 | 4,
          lineVisible: !dots,
          pointMarkersVisible: dots,
          ...(dots ? { pointMarkersRadius: Math.max(2, style.linewidth) } : {}),
          lineStyle: LW_STYLE[style.linestyle] ?? LwLineStyle.Solid,
          title: '',
          // TradingView shows each plot's latest value on the price scale in the plot's color (not for sparse dots,
          // whose "latest value" is an old event)
          lastValueVisible: !dots,
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
      layers.push({ uid: inst.uid, name: meta.short_name, drawings, shapes, front: meta.behind_chart === false,
        fixedLabels: meta.label_layout === false })
    }
    for (const [key, s] of this.plotSeries) {
      if (!wanted.has(key)) {
        this.chart.removeSeries(s)
        this.plotSeries.delete(key)
        this.paneOf.delete(key)
      }
    }
    this.plotTips = tips
    this.syncPaneTitles(titles)
    this.overlay.layers = layers
    this.overlay.update()
    this.setCandleStyles(candleSources)
  }

  /** Keep one small title in the top-left corner of each grouped indicator pane. */
  private syncPaneTitles(wanted: Map<string, { pane: number; text: string }>): void {
    const panes = this.chart.panes()
    for (const [key, cur] of this.paneTitles) {
      const w = wanted.get(key)
      if (w && w.pane === cur.pane && w.text === cur.text) continue
      try { cur.wm.detach() } catch { /* its pane was already removed */ }
      this.paneTitles.delete(key)
    }
    for (const [key, w] of wanted) {
      if (this.paneTitles.has(key) || !panes[w.pane]) continue
      const wm = createTextWatermark(panes[w.pane], { horzAlign: 'left', vertAlign: 'top',
        lines: [{ text: w.text, color: 'rgba(178, 181, 190, 0.6)', fontSize: 11 }] })
      this.paneTitles.set(key, { ...w, wm })
    }
  }

  /** Plot line under the cursor (within a few pixels at the hovered bar) in the hovered pane. */
  private plotHit(p: MouseEventParams<Time>, pane: number): HitInfo | null {
    if (!p.point) return null
    let best: HitInfo | null = null
    let bestDist = 6
    for (const [s, tip] of this.plotTips) {
      if (tip.pane !== pane) continue
      const d = p.seriesData.get(s) as LineData | undefined
      if (!d || typeof d.value !== 'number') continue
      const y = s.priceToCoordinate(d.value)
      if (y === null) continue
      const dist = Math.abs(y - p.point.y)
      if (dist <= bestDist) {
        bestDist = dist
        best = { title: tip.title, body: tip.body }
      }
    }
    return best
  }

  /** Top of a pane inside the chart element (crosshair points are pane-relative). */
  private paneTop(pane: number): number {
    if (pane === 0) return 0
    const el = this.chart.panes()[pane]?.getHTMLElement()
    const root = this.chart.chartElement()
    return el ? el.getBoundingClientRect().top - root.getBoundingClientRect().top : 0
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
    if (!this.feedId || !Number.isFinite(from) || !Number.isFinite(to)) return
    const focus = ++this.focusSeq
    const feedId = this.feedId
    const tf = this.tf
    const cutoff = this.replayCutoffTime()
    if (cutoff !== null && from > cutoff) {
      this.ev.onNotice('The requested time is after the replay cursor. Exit replay to view it.')
      return
    }
    const margin = 40 * TF_SECONDS[tf]
    if (this.loading || !this.bars.length || (cutoff === null && (from < this.bars[0][0] + margin
        || to > this.bars[this.bars.length - 1][0]))) {
      const loaded = await this.load(feedId, tf, cutoff, from)
      if (!loaded) return
    }
    // Replay loads around its cursor, and the window has to stay continuous up to it: an earlier date is reached by
    // paging older history in (reloading around the date would drop the bars between it and the cursor).
    while (cutoff !== null && this.hasMore && this.bars.length && from < this.bars[0][0] + margin) {
      const first = this.bars[0][0]
      await this.loadMore(Math.min(20000, Math.ceil((first - from + margin) / TF_SECONDS[tf]) + PAGE))
      if (focus !== this.focusSeq || this.feedId !== feedId || this.tf !== tf || this.disposed) return
      if (!this.bars.length || this.bars[0][0] === first) break
    }
    if (focus !== this.focusSeq || this.feedId !== feedId || this.tf !== tf || this.disposed) return
    const times = this.times
    if (!times.length) return
    const previous = indexAtOrBefore(times, from)
    let a = Math.max(0, previous)
    if (previous < 0 || from >= times[a] + TF_SECONDS[tf]) {
      const next = previous + 1
      if (next < times.length) {
        a = next
        this.ev.onNotice(`No candle at ${fmtDateTime(from, 'UTC')} UTC. Showing the next available candle: ${fmtDateTime(times[a], 'UTC')} UTC.`)
      } else {
        this.ev.onNotice(`No candle at ${fmtDateTime(from, 'UTC')} UTC. Showing the last available candle: ${fmtDateTime(times[a], 'UTC')} UTC.`)
      }
    } else this.ev.onNotice(null)
    const b = Math.max(a, indexAtOrBefore(times, to))
    const pad = Math.max(25, Math.round((b - a) * 0.6))
    this.chart.timeScale().setVisibleLogicalRange({ from: a - pad, to: b + pad })
    // Mark the candle landed on, and put the crosshair on it: the mouse rests over the chart after the dialog
    // closes, and its crosshair label (another candle's time) read as the jump landing at the wrong time
    this.overlay.focusTime = times[a]
    this.overlay.update()
    this.chart.setCrosshairPosition(this.shownBars[a][4], times[a] as UTCTimestamp, this.candles)
  }

  // ------------------------------------------------------------------- replay
  startReplay(index: number): void {
    const range = this.chart.timeScale().getVisibleLogicalRange()
    this.forming = null
    this.replayIndex = Math.max(0, Math.min(index, this.bars.length - 1))
    this.updateReplayCutoff()
    void this.prefetchSub(this.replayIndex + 1)
    this.render()
    // keep the picked bar where it was; upcoming replay bars fill the empty space to its right
    if (range) this.chart.timeScale().setVisibleLogicalRange(range)
  }

  stepReplay(): boolean {
    if (this.loading || this.replayIndex === null) return false
    if (this.replayIndex >= this.bars.length - 1) { void this.loadForward(); return false }
    if (!this.forming) this.followNewBar()
    this.forming = null // the full bar replaces any partly formed one (same time)
    this.replayIndex++
    this.updateReplayCutoff()
    if (this.replayIndex >= this.bars.length - 30) void this.loadForward()
    const row = this.bars[this.replayIndex]
    this.candles.update(this.toCandle(row))
    this.syncOverlayBars()
    this.version++
    this.ev.onData()
    return true
  }

  /** One bar back: candles can only be appended in place, so the shown range is redrawn. */
  stepBackReplay(): boolean {
    if (this.loading || this.replayIndex === null) return false
    const range = this.chart.timeScale().getVisibleLogicalRange()
    if (this.forming) this.forming = null // back to the last completed bar
    else if (this.replayIndex > 0) this.replayIndex--
    else return false
    this.updateReplayCutoff()
    this.render()
    if (range) this.chart.timeScale().setVisibleLogicalRange(range)
    return true
  }

  stopReplay(): void {
    this.chart.timeScale().applyOptions({ shiftVisibleRangeOnNewBar: true })
    if ((this.hasNewer || this.loading) && this.feedId) {
      void this.load(this.feedId, this.tf)
      return
    }
    this.forming = null
    this.replayIndex = null
    this.replayCutoff = null
    this.render()
    this.chart.timeScale().scrollToRealTime()
  }

  /**
   * Like TradingView replay: while there is free space on the right, a new bar fills it and the chart stays
   * still (scrolling every bar shifted all drawings, and labels anchored to the last bar jumped back once the
   * indicator caught up, which looked like blinking). Near the right edge the chart follows bar by bar.
   */
  private followNewBar(): void {
    const ts = this.chart.timeScale()
    const last = this.shownBars[this.shownBars.length - 1]
    const x = last ? ts.timeToCoordinate(last[0] as UTCTimestamp) : null
    ts.applyOptions({ shiftVisibleRangeOnNewBar: x === null || x > ts.width() - 10 * ts.options().barSpacing })
  }

  /** Open time of the last completed replay bar: indicators are computed up to here, never the forming one. */
  replayCursorTime(): number | null {
    return this.replayIndex === null ? null : this.bars[this.replayIndex]?.[0] ?? null
  }

  replayCutoffTime(): number | null { return this.replayCutoff }

  private updateReplayCutoff(): void {
    const time = this.replayCursorTime()
    const alignment = useApp.getState().feeds.find((f) => f.id === this.feedId)?.alignment
    this.replayCutoff = time === null ? null : barCloseTime(time, this.tf, alignment)
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
  advanceReplay(maxTicks: number): 'tick' | 'bar' | 'end' | 'loading' {
    if (this.loading) return 'loading'
    if (this.replayIndex === null) return 'end'
    if (this.replayIndex >= this.bars.length - 1) {
      if (this.hasNewer) { void this.loadForward(); return 'loading' }
      return 'end'
    }
    if (maxTicks <= 1 && !this.forming) return this.stepReplay() ? 'bar' : 'end'
    if (!this.forming) {
      const next = this.replayIndex + 1
      this.followNewBar()
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
    const seq = this.loadSeq
    const span = TF_SECONDS[chartTf]
    const from = this.bars[index][0]
    const last = Math.min(this.bars.length - 1, index + 99)
    const to = this.bars[last][0] + span
    const limit = Math.min(20000, Math.ceil((to - from) / TF_SECONDS[tf]) + 2)
    this.subLoading = api.candles(feedId, tf, { before: to, limit }).then((res) => {
      if (seq !== this.loadSeq || this.feedId !== feedId || this.tf !== chartTf) return
      const rows = res.bars.filter((r) => r[0] >= from && r[0] < to && r[6] !== 0)
      // a full page may not reach back to ``from``: then coverage starts at its first candle
      const start = res.bars.length >= limit && rows.length ? rows[0][0] : from
      this.sub = { feedId, tf, chartTf, from: start, to, rows }
    }).catch(() => { /* movement falls back to the candle's own OHLC path */ }).finally(() => { if (seq === this.loadSeq) this.subLoading = null })
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
