import { describe, expect, it } from 'vitest'
import { AnnotationLayout, type AnnotationRect } from './annotationLayout'

function assertSeparated(rects: AnnotationRect[]) {
  for (let i = 0; i < rects.length; i++) {
    for (const b of rects.slice(i + 1)) {
      const a = rects[i]
      expect(a.x + a.width + 3 <= b.x || b.x + b.width + 3 <= a.x
        || a.y + a.height + 3 <= b.y || b.y + b.height + 3 <= a.y).toBe(true)
    }
  }
}

describe('annotation layout', () => {
  it('separates coincident LUS/SS labels and overlapping neighbouring candles at different zooms', () => {
    for (const spacing of [2, 7, 20, 60]) {
      const layout = new AnnotationLayout(800, 600)
      const rects = Array.from({ length: 12 }, (_, i) => layout.place({
        x: 240 + Math.floor(i / 2) * spacing, y: 400, width: i % 2 ? 22 : 34, height: 25,
      }, -1))
      assertSeparated(rects)
      expect(rects.every((r) => r.y + r.height <= 425)).toBe(true)
    }
  })

  it('shares space between multiline labels, marker glyphs and marker text across layers', () => {
    const layout = new AnnotationLayout(500, 400)
    const rects = [
      layout.place({ x: 200, y: 200, width: 70, height: 45 }, 1),
      layout.place({ x: 220, y: 202, width: 12, height: 12 }, 1),
      layout.place({ x: 218, y: 203, width: 52, height: 40 }, 1),
      layout.place({ x: 201, y: 202, width: 34, height: 25 }, -1),
    ]
    assertSeparated(rects)
    expect(rects[1].y).toBeGreaterThan(rects[0].y + rects[0].height)
  })

  it('keeps crowded edge annotations inside the pane using adjacent columns', () => {
    const layout = new AnnotationLayout(240, 140)
    const rects = Array.from({ length: 15 }, () => layout.place({ x: -10, y: -10, width: 34, height: 20 }, -1))
    assertSeparated(rects)
    expect(rects.every((r) => r.x >= 0 && r.x + r.width <= 240 && r.y >= 0 && r.y + r.height <= 140)).toBe(true)
  })

  it('rebuilds deterministically on repaint without changing source coordinates', () => {
    const source = { x: 100, y: 100, width: 34, height: 25 }
    const render = () => {
      const layout = new AnnotationLayout(500, 400)
      return [layout.place(source, -1), layout.place(source, -1)]
    }
    expect(render()).toEqual(render())
    expect(source).toEqual({ x: 100, y: 100, width: 34, height: 25 })
  })
})
