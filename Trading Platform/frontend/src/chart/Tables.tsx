import type { CSSProperties } from 'react'
import type { IndicatorTable } from '../api/types'
import { cssColor } from '../lib/colors'
import type { ChartController } from './controller'

/** Pine tables of the visible indicators, pinned to their position inside the price pane. */
export function IndicatorTables({ ctrl, tables }: { ctrl: ChartController; tables: IndicatorTable[]; version: number }) {
  if (!tables.length) return null
  const byPos = new Map<string, IndicatorTable[]>()
  for (const t of tables) byPos.set(t.position, [...(byPos.get(t.position) ?? []), t])
  // keep clear of the price/time axes and of the scroll-to-realtime button (bottom right)
  const right = ctrl.chart.priceScale('right').width() + 6
  const bottom = ctrl.chart.timeScale().height() + 36
  return (
    <>
      {[...byPos].map(([pos, list]) => {
        const [v, h] = pos.split('_')
        const style: CSSProperties = {}
        if (h === 'left') style.left = 10
        else if (h === 'right') style.right = right
        else style.left = '50%'
        if (v === 'top') style.top = 54
        else if (v === 'bottom') style.bottom = bottom
        else style.top = '50%'
        style.transform = `translate(${h === 'center' ? '-50%' : '0'}, ${v === 'middle' ? '-50%' : '0'})`
        return (
          <div key={pos} className="ind-tables" style={style}>
            {list.map((t) => (
              <table key={t.id} className="ind-table">
                <tbody>
                  {t.rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((c, ci) => (
                        <td key={ci} title={c.tooltip || undefined}
                          style={{ background: cssColor(c.bg_color), color: cssColor(c.text_color) }}>{c.text}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
          </div>
        )
      })}
    </>
  )
}
