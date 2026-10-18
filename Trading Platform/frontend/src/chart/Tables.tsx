import type { CSSProperties } from 'react'
import type { IndicatorTable, TableCell } from '../api/types'
import { cssColor } from '../lib/colors'
import type { ChartController } from './controller'

/** Pine tables of the visible indicators, pinned to their position inside the price pane. */
export function IndicatorTables({ ctrl, tables }: { ctrl: ChartController; tables: IndicatorTable[]; version: number }) {
  if (!tables.length) return null
  const byPos = new Map<string, IndicatorTable[]>()
  for (const t of tables) byPos.set(t.position, [...(byPos.get(t.position) ?? []), t])
  // keep clear of the price and time axes
  const right = ctrl.chart.priceScale('right').width() + 6
  const bottom = ctrl.chart.timeScale().height() + 6
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
              <table key={t.id} className="ind-table" style={tableStyle(t)}>
                <tbody>
                  {t.rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((c, ci) => (
                        <td key={ci} title={c.tooltip || undefined} style={cellStyle(t, c)}>{c.text}</td>
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

// Pine size.* for table text (px)
const TEXT_PX: Record<string, number> = { tiny: 9, small: 10, normal: 12, large: 16, huge: 22, auto: 12 }

/** table.new(bgcolor, frame_*, border_*): only tables that set them are styled; older tables keep plain cells. */
function tableStyle(t: IndicatorTable): CSSProperties | undefined {
  if (t.frame_width === undefined && t.border_width === undefined && !t.bg_color) return undefined
  // collapsed borders with a hidden outer edge: border lines only between cells, the frame drawn around them
  const style: CSSProperties = { borderCollapse: 'collapse', borderStyle: 'hidden' }
  if (t.bg_color) style.background = cssColor(t.bg_color)
  if (t.frame_width && t.frame_color) style.outline = `${t.frame_width}px solid ${cssColor(t.frame_color)}`
  if (t.frame_width) style.margin = t.frame_width
  return style
}

function cellStyle(t: IndicatorTable, c: TableCell): CSSProperties {
  const style: CSSProperties = { background: cssColor(c.bg_color), color: cssColor(c.text_color) }
  if (t.border_width && t.border_color) style.border = `${t.border_width}px solid ${cssColor(t.border_color)}`
  if (c.text_size) {
    style.fontSize = TEXT_PX[c.text_size] ?? 12
    if (!c.text) style.padding = c.text_size === 'tiny' ? '1px 6px' : undefined
  }
  if (c.text_halign) style.textAlign = c.text_halign
  if (c.text_valign) style.verticalAlign = c.text_valign === 'center' ? 'middle' : c.text_valign
  if (c.text.includes('\n')) style.whiteSpace = 'pre'
  return style
}
