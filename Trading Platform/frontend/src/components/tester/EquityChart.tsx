import { useEffect, useRef } from 'react'
import {
  BaselineSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { SeriesPoints } from '../../api/types'
import { THEME } from '../../lib/colors'
import { fmtCrosshair, fmtTick, type TimezoneId } from '../../lib/format'

const STRAT_COLORS = ['#2962FF', '#FF9800', '#AB47BC', '#26A69A', '#EF5350', '#FFEB3B']

function points(s: SeriesPoints) {
  const out: { time: UTCTimestamp; value: number }[] = []
  let last = -Infinity
  s.times.forEach((t, i) => {
    if (t > last) {
      out.push({ time: t as UTCTimestamp, value: s.values[i] })
      last = t
    }
  })
  return out
}

/** Mark-to-market equity (bar closes) with the drawdown from the running peak as columns underneath. */
export function EquityChart({ equity, drawdown, extra, capital, tz }: {
  equity: SeriesPoints
  drawdown: SeriesPoints | null
  extra: { title: string; series: SeriesPoints }[]
  capital: number
  tz: TimezoneId
}) {
  const host = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const chart = createChart(host.current!, {
      autoSize: true,
      layout: { background: { color: THEME.bg }, textColor: THEME.muted, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: THEME.grid } },
      rightPriceScale: { borderColor: THEME.border, scaleMargins: { top: 0.08, bottom: 0.26 } },
      timeScale: { borderColor: THEME.border, timeVisible: true },
      crosshair: { vertLine: { labelBackgroundColor: '#363a45' }, horzLine: { labelBackgroundColor: '#363a45' } },
      handleScroll: true,
      handleScale: true,
    })
    chartRef.current = chart
    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.applyOptions({
      localization: { timeFormatter: (t: Time) => fmtCrosshair(t as number, tz, true) },
      timeScale: { tickMarkFormatter: (t: Time, type: number) => fmtTick(t as number, tz, type) },
    })
    const created: ReturnType<IChartApi['addSeries']>[] = []
    const eq = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: capital },
      topLineColor: THEME.up, topFillColor1: 'rgba(8,153,129,0.28)', topFillColor2: 'rgba(8,153,129,0.03)',
      bottomLineColor: THEME.down, bottomFillColor1: 'rgba(242,54,69,0.03)', bottomFillColor2: 'rgba(242,54,69,0.28)',
      lineWidth: 2, priceLineVisible: false, title: 'Equity',
    })
    eq.setData(points(equity))
    created.push(eq)
    extra.forEach((x, i) => {
      const s = chart.addSeries(LineSeries, { color: STRAT_COLORS[i % STRAT_COLORS.length], lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false, title: x.title })
      s.setData(points(x.series))
      created.push(s)
    })
    if (drawdown) {
      // TradingView-style: drawdown columns hang from the top of a band at the bottom of the pane
      const dd = chart.addSeries(HistogramSeries, { color: 'rgba(242,54,69,0.45)', priceLineVisible: false,
        lastValueVisible: false, title: '', priceScaleId: 'dd', priceFormat: { type: 'price', precision: 2, minMove: 0.01 } })
      chart.priceScale('dd').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 }, visible: false })
      dd.setData(points(drawdown))
      created.push(dd)
    }
    chart.timeScale().fitContent()
    return () => {
      if (chartRef.current !== chart) return // the chart itself was already disposed
      for (const s of created) chart.removeSeries(s)
    }
  }, [equity, drawdown, extra, capital, tz])

  return <div ref={host} className="equity-chart" />
}
