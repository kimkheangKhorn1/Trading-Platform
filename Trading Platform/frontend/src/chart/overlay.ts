/**
 * Custom Lightweight Charts series primitive that renders the indicator engine's drawing objects
 * (Pine box/line/label, plotshape markers) and the Strategy Tester's trade overlays.
 *
 * Everything the engine outputs is positioned by UTC time + price; this layer only converts
 * those coordinates to pixels (see timeIndex.ts) and paints them. Annotation bodies may move
 * to prevent collisions; connector lines retain the original time/price anchor. Values never change.
 */
import type {
  AutoscaleInfo,
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts'
import type { DrawingObj, LabelObj, ShapePoint, TradeDto } from '../api/types'
import { cssColor } from '../lib/colors'
import { indexAtOrBefore, timeToLogical } from './timeIndex'
import { AnnotationLayout, type AnnotationRect } from './annotationLayout'

export interface ShapeLayer {
  id: string
  title: string
  shape: string
  location: 'abovebar' | 'belowbar' | 'absolute'
  color: string
  text: string
  textColor: string
  size: string
  points: ShapePoint[]
}

export interface OverlayLayer {
  uid: string
  name: string
  drawings: DrawingObj[]
  shapes: ShapeLayer[]
}

export interface TradeOverlay {
  trades: TradeDto[]
  selectedId: string | null
  showAll: boolean
  strategyTitles: Record<string, string>
}

export interface HitInfo {
  title: string
  body: string
  tradeId?: string
}

interface HitRegion {
  x0: number
  y0: number
  x1: number
  y1: number
  info: HitInfo
  priority: number
  segment?: [number, number, number, number]
}

interface AnnotationPaint {
  rect: AnnotationRect
  original: AnnotationRect
  x: number
  y: number
  color: string
  paint: () => void
}

type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer['draw']>[0]

const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif'
const SIZE_PX: Record<string, number> = { tiny: 6, small: 9, normal: 12, auto: 9, large: 16, huge: 22 }
const FONT_PX: Record<string, number> = { tiny: 9, small: 10, normal: 11, auto: 10, large: 13, huge: 16 }

function dash(ctx: CanvasRenderingContext2D, style: string, width: number) {
  if (style === 'dashed') ctx.setLineDash([5 * Math.max(1, width * 0.8), 4])
  else if (style === 'dotted') ctx.setLineDash([Math.max(1, width), 3])
  else ctx.setLineDash([])
}

export class OverlayPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null
  private series: ISeriesApi<SeriesType> | null = null
  private requestUpdate: (() => void) | null = null
  private readonly view: IPrimitivePaneView
  private readonly bgView: IPrimitivePaneView
  private readonly lineView: IPrimitivePaneView

  times: number[] = []
  highs: number[] = []
  lows: number[] = []
  tfSeconds = 60
  precision = 3
  layers: OverlayLayer[] = []
  trades: TradeOverlay | null = null
  // hit regions recorded while painting (media coordinates), one list per pass
  private bgRegions: HitRegion[] = []
  private lineRegions: HitRegion[] = []
  private regions: HitRegion[] = []

  constructor() {
    this.view = {
      zOrder: () => 'top',
      renderer: (): IPrimitivePaneRenderer => ({ draw: (t) => this.draw(t, 'annotations') }),
    }
    this.bgView = {
      // Lightweight Charts paints 'bottom' before the grid. The normal background
      // pass comes after the grid but before candles, volume and indicator plots.
      zOrder: () => 'normal',
      renderer: (): IPrimitivePaneRenderer => ({
        drawBackground: (t) => this.draw(t, 'background'),
        draw: () => {},
      }),
    }
    this.lineView = {
      zOrder: () => 'normal',
      renderer: (): IPrimitivePaneRenderer => ({ draw: (t) => this.draw(t, 'lines') }),
    }
  }

  attached(p: SeriesAttachedParameter<Time>): void {
    this.chart = p.chart as IChartApi
    this.series = p.series as ISeriesApi<SeriesType>
    this.requestUpdate = p.requestUpdate
  }

  detached(): void {
    this.chart = null
    this.series = null
    this.requestUpdate = null
  }

  paneViews() {
    return [this.bgView, this.lineView, this.view]
  }

  update(): void {
    this.requestUpdate?.()
  }

  /** Keep the inspected trade's entry, stop and targets inside the price scale while it is on screen. */
  autoscaleInfo(start: Logical, end: Logical): AutoscaleInfo | null {
    const sel = this.trades?.trades.find((t) => t.id === this.trades?.selectedId)
    if (!sel || this.times.length === 0) return null
    const a = timeToLogical(this.times, sel.signal_time, this.tfSeconds)
    const b = timeToLogical(this.times, sel.exit_time ?? this.times[this.times.length - 1], this.tfSeconds)
    if (b < start || a > end) return null
    const prices = [sel.entry_price, sel.stop_loss, sel.final_stop, ...sel.take_profits]
    return { priceRange: { minValue: Math.min(...prices), maxValue: Math.max(...prices) } }
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const h = this.hit(x, y)
    return h ? { externalId: h.tradeId ?? 'overlay', zOrder: 'top', cursorStyle: h.tradeId ? 'pointer' : 'help' } : null
  }

  /** Topmost tooltip region under the cursor (media coordinates). */
  hit(x: number, y: number): HitInfo | null {
    let best: HitRegion | null = null
    let bestDist = Infinity
    for (const r of [...this.bgRegions, ...this.lineRegions, ...this.regions]) {
      let d: number
      if (r.segment) {
        d = distToSegment(x, y, ...r.segment)
        if (d > 4) continue
      } else {
        if (x < r.x0 || x > r.x1 || y < r.y0 || y > r.y1) continue
        d = 0
      }
      if (!best || r.priority > best.priority || (r.priority === best.priority && d < bestDist)) {
        best = r
        bestDist = d
      }
    }
    return best ? best.info : null
  }

  // ------------------------------------------------------------------ coordinates
  private x(t: number): number | null {
    const ts = this.chart?.timeScale()
    if (!ts) return null
    return ts.logicalToCoordinate(timeToLogical(this.times, t, this.tfSeconds) as Logical)
  }

  private y(p: number): number | null {
    return this.series?.priceToCoordinate(p) ?? null
  }

  private barIndex(t: number): number {
    const i = indexAtOrBefore(this.times, t)
    return i >= 0 && this.times[i] === t ? i : -1
  }

  // ------------------------------------------------------------------------- draw
  private draw(target: CanvasRenderingTarget2D, pass: 'background' | 'lines' | 'annotations'): void {
    if (!this.chart || !this.series || this.times.length === 0) return
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const range = this.chart!.timeScale().getVisibleLogicalRange()
      if (!range) return
      const lo = Math.floor(range.from) - 2
      const hi = Math.ceil(range.to) + 2
      const t0 = this.times[Math.max(0, Math.min(this.times.length - 1, lo))]
      const t1 = hi >= this.times.length ? Infinity : this.times[Math.max(0, hi)]
      if (pass === 'background') {
        this.bgRegions = []
        for (const layer of this.layers) this.drawBoxes(ctx, layer, mediaSize.width, t0, t1)
        if (this.trades) this.drawTradeZones(ctx, t0, t1)
        return
      }
      if (pass === 'lines') {
        this.lineRegions = []
        for (const layer of this.layers) this.drawLines(ctx, layer, mediaSize.width)
        if (this.trades) this.drawTrades(ctx, mediaSize.width, t0, t1)
        return
      }
      this.regions = []
      const layout = new AnnotationLayout(mediaSize.width, mediaSize.height)
      const annotations: AnnotationPaint[] = []
      // One layout for all layers, including neighbouring bars. Reserve label text before markers.
      for (const layer of this.layers) this.drawLabels(ctx, layer, t0, t1, layout, mediaSize.height, annotations)
      for (const layer of this.layers) this.drawShapes(ctx, layer, t0, t1, layout, mediaSize.height, annotations)
      // Protect every annotation, including transparent text labels, from all connector strokes.
      ctx.save()
      ctx.beginPath()
      ctx.rect(0, 0, mediaSize.width, mediaSize.height)
      for (const { rect } of annotations) ctx.rect(rect.x - 1, rect.y - 1, rect.width + 2, rect.height + 2)
      ctx.clip('evenodd')
      for (const a of annotations) this.drawConnector(ctx, a.x, a.y, a.rect, a.original, a.color)
      ctx.restore()
      for (const a of annotations) a.paint()
    })
  }

  private drawBoxes(ctx: CanvasRenderingContext2D, layer: OverlayLayer, width: number, t0: number, t1: number) {
    for (const d of layer.drawings) {
      if (d.kind !== 'box') continue
      const right = d.extend === 'right' || d.extend === 'both' ? Infinity : d.right
      if (right < t0 || d.left > t1) continue
      const xa = d.extend === 'left' || d.extend === 'both' ? 0 : this.x(d.left)
      const xb = right === Infinity ? width : this.x(d.right)
      const ya = this.y(d.top)
      const yb = this.y(d.bottom)
      if (xa === null || xb === null || ya === null || yb === null) continue
      const x0 = Math.min(xa, xb)
      const y0 = Math.min(ya, yb)
      const w = Math.abs(xb - xa)
      const h = Math.max(1, Math.abs(yb - ya))
      ctx.fillStyle = cssColor(d.bg_color)
      ctx.fillRect(x0, y0, w, h)
      if (d.border_width > 0) {
        // Hairline borders: half the width in device pixels (1 physical px for width 1 on Retina),
        // snapped to the pixel grid so the edge is not blurred across neighbouring pixels.
        const px = ctx.getTransform().a || 1
        const devWidth = Math.max(1, Math.round((d.border_width * px) / 2))
        const snap = (v: number) => (Math.round(v * px) + (devWidth % 2) / 2) / px
        const sx0 = snap(x0)
        const sy0 = snap(y0)
        ctx.strokeStyle = cssColor(d.border_color)
        ctx.lineWidth = devWidth / px
        dash(ctx, d.border_style, d.border_width)
        ctx.strokeRect(sx0, sy0, snap(x0 + w) - sx0, snap(y0 + h) - sy0)
        ctx.setLineDash([])
      }
      if (d.text) {
        ctx.font = `${FONT_PX[d.text_size] ?? 10}px ${FONT}`
        ctx.fillStyle = cssColor(d.text_color)
        ctx.textBaseline = 'middle'
        ctx.textAlign = d.text_halign === 'left' ? 'left' : d.text_halign === 'right' ? 'right' : 'center'
        const tx = d.text_halign === 'left' ? x0 + 4 : d.text_halign === 'right' ? x0 + w - 4 : x0 + w / 2
        ctx.fillText(d.text, tx, y0 + h / 2)
      }
      if (d.tooltip || d.text) {
        this.bgRegions.push({ x0, y0, x1: x0 + w, y1: y0 + h, priority: 0,
          info: { title: layer.name, body: d.tooltip || d.text } })
      }
    }
  }

  private drawLines(ctx: CanvasRenderingContext2D, layer: OverlayLayer, width: number) {
    for (const d of layer.drawings) {
      if (d.kind !== 'line') continue
      let xa = this.x(d.x1)
      let xb = this.x(d.x2)
      let ya = this.y(d.y1)
      let yb = this.y(d.y2)
      if (xa === null || xb === null || ya === null || yb === null) continue
      if (d.extend !== 'none' && xb !== xa) {
        const slope = (yb - ya) / (xb - xa)
        if (d.extend === 'right' || d.extend === 'both') {
          const nx = xb > xa ? width : 0
          yb = yb + slope * (nx - xb)
          xb = nx
        }
        if (d.extend === 'left' || d.extend === 'both') {
          const nx = xb > xa ? 0 : width
          ya = ya + slope * (nx - xa)
          xa = nx
        }
      }
      if (Math.max(xa, xb) < 0 || Math.min(xa, xb) > width) continue
      ctx.strokeStyle = cssColor(d.color)
      ctx.lineWidth = d.width
      dash(ctx, d.style, d.width)
      ctx.beginPath()
      ctx.moveTo(xa, ya)
      ctx.lineTo(xb, yb)
      ctx.stroke()
      ctx.setLineDash([])
      if (d.tooltip) {
        this.lineRegions.push({ x0: 0, y0: 0, x1: 0, y1: 0, priority: 1, segment: [xa, ya, xb, yb],
          info: { title: layer.name, body: d.tooltip } })
      }
    }
  }

  private shapeY(location: string, idx: number, price: number | null, size: number) {
    if (location === 'absolute' && price !== null) return this.y(price)
    const above = location === 'abovebar'
    const base = this.y(above ? this.highs[idx] : this.lows[idx])
    if (base === null) return null
    return above ? base - 6 - size : base + 6 + size
  }

  private drawShapes(ctx: CanvasRenderingContext2D, layer: OverlayLayer, t0: number, t1: number,
    layout: AnnotationLayout, height: number, annotations: AnnotationPaint[]) {
    const ts = this.chart!.timeScale()
    for (const s of layer.shapes) {
      const pts = s.points
      let a = lowerBound(pts, t0)
      for (; a < pts.length && pts[a].time <= t1; a++) {
        const p = pts[a]
        const idx = this.barIndex(p.time)
        if (idx < 0) continue
        const x = ts.logicalToCoordinate(idx as Logical)
        const size = SIZE_PX[s.size] ?? 9
        const y = this.shapeY(s.location, idx, p.price, size / 2 + 1)
        if (x === null || y === null || x < 0 || x > this.chart!.timeScale().width() || y < -size || y > height + size) continue
        const color = cssColor(p.color ?? s.color)
        const text = p.text ?? s.text
        const fpx = FONT_PX[s.size] ?? 10
        ctx.font = `${fpx}px ${FONT}`
        const above = s.location === 'abovebar'
        const lines = text ? text.split('\n') : []
        const textWidth = lines.length ? Math.max(...lines.map((l) => ctx.measureText(l).width)) : 0
        const textHeight = lines.length * (fpx + 3)
        const w = Math.max(size + 3, textWidth + 4)
        const original = { x: x - w / 2, y: above && text ? y - size - textHeight : y - size / 2 - 1.5,
          width: w, height: size + 3 + (text ? size / 2 + textHeight : 0) }
        const placed = layout.place(original, above ? -1 : 1)
        const px = x + placed.x - original.x
        const py = y + placed.y - original.y
        annotations.push({ rect: placed, original, x, y, color, paint: () => {
          paintShape(ctx, s.shape, px, py, size, color)
          if (text) {
            ctx.font = `${fpx}px ${FONT}`
            ctx.fillStyle = cssColor(s.textColor)
            ctx.textAlign = 'center'
            ctx.textBaseline = 'top'
            const ty = above ? py - size - textHeight : py + size
            lines.forEach((line, k) => ctx.fillText(line, px, ty + k * (fpx + 3)))
          }
        } })
        this.regions.push({ x0: placed.x, y0: placed.y, x1: placed.x + placed.width, y1: placed.y + placed.height, priority: 2,
          info: { title: `${layer.name} · ${s.title}`, body: p.tooltip ?? text ?? '' } })
      }
    }
  }

  private drawLabels(ctx: CanvasRenderingContext2D, layer: OverlayLayer, t0: number, t1: number,
    layout: AnnotationLayout, height: number, annotations: AnnotationPaint[]) {
    for (const d of layer.drawings) {
      if (d.kind !== 'label' || d.x < t0 || d.x > t1) continue
      this.drawLabel(ctx, layer, d, layout, height, annotations)
    }
  }

  private drawLabel(ctx: CanvasRenderingContext2D, layer: OverlayLayer, d: LabelObj,
    layout: AnnotationLayout, height: number, annotations: AnnotationPaint[]) {
    const x = this.x(d.x)
    if (x === null || x < 0 || x > this.chart!.timeScale().width()) return
    const idx = this.barIndex(d.x)
    let y: number | null
    if (d.yloc === 'abovebar' && idx >= 0) y = (this.y(this.highs[idx]) ?? 0) - 6
    else if (d.yloc === 'belowbar' && idx >= 0) y = (this.y(this.lows[idx]) ?? 0) + 6
    else y = this.y(d.y)
    if (y === null || y < -6 || y > height + 6) return
    const fpx = FONT_PX[d.size] ?? 11
    ctx.font = `${fpx}px ${FONT}`
    const lines = d.text ? d.text.split('\n') : []
    const tw = lines.length ? Math.max(...lines.map((l) => ctx.measureText(l).width)) : 0
    const lh = fpx + 3
    const w = Math.max(tw + 10, 14)
    const h = Math.max(lines.length * lh + 4, 14)
    const ptr = 5
    let bx = x - w / 2
    let by = y
    const st = d.style
    if (st === 'label_down') by = y - ptr - h
    else if (st === 'label_up') by = y + ptr
    else if (st === 'label_left') { bx = x + ptr; by = y - h / 2 }
    else if (st === 'label_right') { bx = x - ptr - w; by = y - h / 2 }
    else by = y - h / 2
    const textOnly = st === 'none'
    const shapeStyle = ['triangleup', 'triangledown', 'arrowup', 'arrowdown', 'circle', 'square', 'diamond', 'cross',
      'xcross', 'flag'].includes(st)
    const glyph = SIZE_PX[d.size] ?? 12
    let original: AnnotationRect
    if (shapeStyle) {
      const sw = Math.max(glyph + 3, tw + 4)
      original = { x: x - sw / 2, y: y - glyph / 2 - 1.5, width: sw,
        height: glyph + 3 + (lines.length ? 4 + lines.length * lh : 0) }
    } else {
      // Include the small pointer in collision bounds; text-only labels need only their body.
      original = { x: Math.min(bx, textOnly ? bx : x - ptr), y: Math.min(by, textOnly ? by : y),
        width: Math.max(bx + w, textOnly ? bx + w : x + ptr) - Math.min(bx, textOnly ? bx : x - ptr),
        height: Math.max(by + h, textOnly ? by + h : y) - Math.min(by, textOnly ? by : y) }
    }
    const direction = d.yloc === 'belowbar' || st === 'label_up' ? 1 : -1
    const placed = layout.place(original, direction)
    const dx = placed.x - original.x
    const dy = placed.y - original.y
    const px = x + dx
    const py = y + dy
    bx += dx
    by += dy
    annotations.push({ rect: placed, original, x, y, color: cssColor(d.text_color), paint: () => {
      ctx.font = `${fpx}px ${FONT}`
      if (shapeStyle) {
        paintShape(ctx, st, px, py, glyph, cssColor(d.color))
        if (d.text) {
          ctx.fillStyle = cssColor(d.text_color)
          ctx.textAlign = 'center'
          ctx.textBaseline = 'top'
          lines.forEach((l, k) => ctx.fillText(l, px, py + glyph / 2 + 4 + k * lh))
        }
      } else {
        if (!textOnly) {
          ctx.fillStyle = cssColor(d.color)
          ctx.beginPath()
          roundRect(ctx, bx, by, w, h, 3)
          ctx.fill()
          ctx.beginPath()
          if (st === 'label_down') { ctx.moveTo(px - ptr, by + h); ctx.lineTo(px, py); ctx.lineTo(px + ptr, by + h) }
          else if (st === 'label_up') { ctx.moveTo(px - ptr, by); ctx.lineTo(px, py); ctx.lineTo(px + ptr, by) }
          else if (st === 'label_left') { ctx.moveTo(bx, py - ptr); ctx.lineTo(px, py); ctx.lineTo(bx, py + ptr) }
          else if (st === 'label_right') { ctx.moveTo(bx + w, py - ptr); ctx.lineTo(px, py); ctx.lineTo(bx + w, py + ptr) }
          ctx.fill()
        }
        ctx.fillStyle = cssColor(d.text_color)
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        lines.forEach((l, k) => ctx.fillText(l, bx + w / 2, by + 2 + lh / 2 + k * lh))
      }
    } })
    if (d.tooltip || d.text) {
      this.regions.push({ x0: placed.x, y0: placed.y, x1: placed.x + placed.width, y1: placed.y + placed.height, priority: 3,
        info: { title: layer.name, body: d.tooltip || d.text } })
    }
  }

  private drawConnector(ctx: CanvasRenderingContext2D, x: number, y: number,
    placed: AnnotationRect, original: AnnotationRect, color: string) {
    if (Math.abs(placed.x - original.x) < 0.5 && Math.abs(placed.y - original.y) < 0.5) return
    ctx.save()
    ctx.strokeStyle = color
    ctx.globalAlpha = 0.55
    ctx.lineWidth = 1
    ctx.setLineDash([2, 2])
    ctx.beginPath()
    ctx.moveTo(x, y)
    ctx.lineTo(Math.max(placed.x, Math.min(placed.x + placed.width, x)),
      Math.max(placed.y, Math.min(placed.y + placed.height, y)))
    ctx.stroke()
    ctx.restore()
  }

  // ------------------------------------------------------------------------ trades
  private tradeTitle(t: TradeDto): string {
    return `${this.trades?.strategyTitles[t.strategy] ?? t.strategy} · ${t.side === 'long' ? 'Long' : 'Short'}`
  }

  private tradeBody(t: TradeDto): string {
    const p = (v: number | null) => (v === null ? '—' : v.toFixed(this.precision))
    return [
      `Entry ${p(t.entry_price)} · Units ${t.units}`,
      `SL ${p(t.stop_loss)}${t.final_stop !== t.stop_loss ? ` → ${p(t.final_stop)}` : ''}`,
      `TP1 ${p(t.tp1)}${t.tp2 !== null ? ` · TP2 ${p(t.tp2)}` : ''}`,
      `Exit ${p(t.exit_price)} (${t.exit_reason})`,
      `P&L ${t.pnl.toFixed(2)} · R ${t.r_multiple === null ? '—' : t.r_multiple.toFixed(2)}${t.ambiguous ? ' · intrabar assumption' : ''}`,
    ].join('\n')
  }

  private drawTradeZones(ctx: CanvasRenderingContext2D, t0: number, t1: number) {
    const tr = this.trades!
    const sel = tr.trades.find((t) => t.id === tr.selectedId)
    if (!sel) return
    const end = sel.exit_time ?? this.times[this.times.length - 1]
    if (end < t0 || sel.entry_time > t1) return
    const xa = this.x(sel.entry_time)
    const xb = this.x(end)
    const ye = this.y(sel.entry_price)
    const ysl = this.y(sel.stop_loss)
    const ytp = this.y(sel.take_profits[sel.take_profits.length - 1] ?? sel.entry_price)
    if (xa === null || xb === null || ye === null || ysl === null || ytp === null) return
    const w = Math.max(3, xb - xa)
    ctx.fillStyle = 'rgba(242,54,69,0.13)'
    ctx.fillRect(xa, Math.min(ye, ysl), w, Math.abs(ysl - ye))
    ctx.fillStyle = 'rgba(8,153,129,0.13)'
    ctx.fillRect(xa, Math.min(ye, ytp), w, Math.abs(ytp - ye))
  }

  private drawTrades(ctx: CanvasRenderingContext2D, width: number, t0: number, t1: number) {
    const tr = this.trades!
    const ts = this.chart!.timeScale()
    const sel = tr.trades.find((t) => t.id === tr.selectedId)
    const list = tr.showAll ? tr.trades : sel ? [sel] : []
    for (const t of list) {
      const end = t.exit_time ?? t.entry_time
      if (end < t0 || t.entry_time > t1) continue
      const isSel = t.id === tr.selectedId
      const color = t.side === 'long' ? '#2962FF' : '#E040FB'
      // entry marker: arrow at the entry price on the entry bar
      const ie = this.barIndex(t.entry_time)
      const xe = ie >= 0 ? ts.logicalToCoordinate(ie as Logical) : this.x(t.entry_time)
      const ye = this.y(t.entry_price)
      if (xe !== null && ye !== null) {
        paintShape(ctx, t.side === 'long' ? 'arrowup' : 'arrowdown', xe, t.side === 'long' ? ye + 9 : ye - 9,
          isSel ? 12 : 9, color)
        this.lineRegions.push({ x0: xe - 7, y0: ye - 16, x1: xe + 7, y1: ye + 16, priority: 4,
          info: { title: this.tradeTitle(t), body: this.tradeBody(t), tradeId: t.id } })
      }
      // exit fills: small squares at each fill price
      for (const f of t.fills) {
        if (f.kind === 'ENTRY') continue
        const xf = this.x(f.time)
        const yf = this.y(f.price)
        if (xf === null || yf === null) continue
        const win = (f.price - t.entry_price) * (t.side === 'long' ? 1 : -1) >= 0
        ctx.fillStyle = win ? '#089981' : '#F23645'
        ctx.fillRect(xf - (isSel ? 4 : 3), yf - (isSel ? 4 : 3), isSel ? 8 : 6, isSel ? 8 : 6)
        if (isSel) {
          ctx.font = `10px ${FONT}`
          ctx.fillStyle = '#D1D4DC'
          ctx.textAlign = 'left'
          ctx.textBaseline = 'middle'
          ctx.fillText(f.kind, xf + 7, yf)
        }
        this.lineRegions.push({ x0: xf - 5, y0: yf - 5, x1: xf + 5, y1: yf + 5, priority: 4,
          info: { title: `${this.tradeTitle(t)} · ${f.kind}`, body: `${f.units} units @ ${f.price.toFixed(this.precision)}`, tradeId: t.id } })
      }
    }
    if (sel) this.drawSelectedLevels(ctx, sel, width)
  }

  private drawSelectedLevels(ctx: CanvasRenderingContext2D, t: TradeDto, width: number) {
    const xa = this.x(t.signal_time)
    const end = t.exit_time ?? this.times[this.times.length - 1]
    const xb = this.x(end)
    if (xa === null || xb === null) return
    const x1 = Math.min(width, xb + 40)
    const levels: [string, number, string, string][] = [
      ['Entry', t.entry_price, '#B2B5BE', 'solid'],
      ['SL', t.stop_loss, '#F23645', 'dashed'],
    ]
    if (t.final_stop !== t.stop_loss) levels.push(['Stop moved', t.final_stop, '#FF9800', 'dotted'])
    t.take_profits.forEach((p, k) => levels.push([`TP${k + 1}`, p, '#089981', k === 0 ? 'dashed' : 'solid']))
    ctx.font = `10px ${FONT}`
    for (const [name, price, color, style] of levels) {
      const y = this.y(price)
      if (y === null) continue
      ctx.strokeStyle = color
      ctx.lineWidth = 1
      dash(ctx, style, 1)
      ctx.beginPath()
      ctx.moveTo(xa, y)
      ctx.lineTo(x1, y)
      ctx.stroke()
      ctx.setLineDash([])
      const txt = `${name} ${price.toFixed(this.precision)}`
      const w = ctx.measureText(txt).width + 8
      ctx.fillStyle = color
      roundRect(ctx, x1 + 2, y - 7, w, 14, 2)
      ctx.fill()
      ctx.fillStyle = '#131722'
      ctx.textAlign = 'left'
      ctx.textBaseline = 'middle'
      ctx.fillText(txt, x1 + 6, y)
    }
    // signal bar marker
    const ys = this.y(t.side === 'long' ? Math.min(t.stop_loss, t.entry_price) : Math.max(t.stop_loss, t.entry_price))
    if (ys !== null) {
      ctx.strokeStyle = 'rgba(255,255,255,0.35)'
      dash(ctx, 'dotted', 1)
      ctx.beginPath()
      ctx.moveTo(xa, 0)
      ctx.lineTo(xa, ctx.canvas.height)
      ctx.stroke()
      ctx.setLineDash([])
    }
  }
}

// ---------------------------------------------------------------------- helpers
function lowerBound(pts: ShapePoint[], t: number): number {
  let lo = 0
  let hi = pts.length
  while (lo < hi) {
    const m = (lo + hi) >> 1
    if (pts[m].time < t) lo = m + 1
    else hi = m
  }
  return lo
}

function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1
  const dy = y2 - y1
  const len = dx * dx + dy * dy
  let t = len ? ((px - x1) * dx + (py - y1) * dy) / len : 0
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

export function paintShape(ctx: CanvasRenderingContext2D, shape: string, x: number, y: number, size: number, color: string) {
  const s = size / 2
  ctx.fillStyle = color
  ctx.strokeStyle = color
  ctx.lineWidth = 1.5
  ctx.setLineDash([])
  ctx.beginPath()
  switch (shape) {
    case 'triangleup':
    case 'labelup':
      ctx.moveTo(x, y - s); ctx.lineTo(x + s, y + s); ctx.lineTo(x - s, y + s); ctx.closePath(); ctx.fill()
      break
    case 'triangledown':
    case 'labeldown':
      ctx.moveTo(x, y + s); ctx.lineTo(x + s, y - s); ctx.lineTo(x - s, y - s); ctx.closePath(); ctx.fill()
      break
    case 'arrowup':
      ctx.moveTo(x, y - s); ctx.lineTo(x + s, y); ctx.lineTo(x + s * 0.4, y); ctx.lineTo(x + s * 0.4, y + s)
      ctx.lineTo(x - s * 0.4, y + s); ctx.lineTo(x - s * 0.4, y); ctx.lineTo(x - s, y); ctx.closePath(); ctx.fill()
      break
    case 'arrowdown':
      ctx.moveTo(x, y + s); ctx.lineTo(x + s, y); ctx.lineTo(x + s * 0.4, y); ctx.lineTo(x + s * 0.4, y - s)
      ctx.lineTo(x - s * 0.4, y - s); ctx.lineTo(x - s * 0.4, y); ctx.lineTo(x - s, y); ctx.closePath(); ctx.fill()
      break
    case 'circle':
      ctx.arc(x, y, s, 0, Math.PI * 2); ctx.fill()
      break
    case 'square':
      ctx.fillRect(x - s, y - s, size, size)
      break
    case 'diamond':
      ctx.moveTo(x, y - s); ctx.lineTo(x + s, y); ctx.lineTo(x, y + s); ctx.lineTo(x - s, y); ctx.closePath(); ctx.fill()
      break
    case 'cross':
      ctx.moveTo(x - s, y); ctx.lineTo(x + s, y); ctx.moveTo(x, y - s); ctx.lineTo(x, y + s); ctx.stroke()
      break
    case 'xcross':
      ctx.moveTo(x - s, y - s); ctx.lineTo(x + s, y + s); ctx.moveTo(x + s, y - s); ctx.lineTo(x - s, y + s); ctx.stroke()
      break
    case 'flag':
      ctx.moveTo(x - s, y + s); ctx.lineTo(x - s, y - s); ctx.lineTo(x + s, y - s * 0.4); ctx.lineTo(x - s, y + s * 0.2)
      ctx.closePath(); ctx.fill()
      break
    default:
      ctx.arc(x, y, s, 0, Math.PI * 2); ctx.fill()
  }
}
