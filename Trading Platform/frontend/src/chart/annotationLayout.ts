/** Screen-space layout shared by every visible indicator's labels and markers. */
export interface AnnotationRect {
  x: number
  y: number
  width: number
  height: number
}

const GAP = 4
const EDGE = 3

export class AnnotationLayout {
  private occupied: AnnotationRect[] = []
  private width: number
  private height: number

  constructor(width: number, height: number) {
    this.width = width
    this.height = height
  }

  place(rect: AnnotationRect, direction: -1 | 1): AnnotationRect {
    const maxX = Math.max(EDGE, this.width - EDGE - rect.width)
    const x = Math.max(EDGE, Math.min(maxX, rect.x))
    const y = Math.max(EDGE, Math.min(this.height - EDGE - rect.height, rect.y))
    const base = { ...rect, x, y }
    const nearby = this.occupied.filter((r) => r.x < x + rect.width + GAP && r.x + r.width + GAP > x)
    const move = (start: AnnotationRect, side: -1 | 1, obstacles: AnnotationRect[]) => {
      const next = { ...start }
      // Jump past the entire conflicting rectangle, including text and pointer space.
      for (;;) {
        const collisions = obstacles.filter((r) => overlaps(next, r))
        if (!collisions.length) return next
        next.y = side === -1
          ? Math.min(...collisions.map((r) => r.y - GAP - next.height))
          : Math.max(...collisions.map((r) => r.y + r.height + GAP))
      }
    }
    let best = move(base, direction, nearby)
    const inside = (r: AnnotationRect) => r.y >= EDGE && r.y + r.height <= this.height - EDGE
    if (!inside(best)) {
      // At pane edges, try the other side and then adjacent columns before overflowing.
      const candidates = [move(base, direction === -1 ? 1 : -1, nearby)]
      const step = rect.width + GAP
      for (let offset = step; offset <= this.width + step; offset += step) {
        for (const cx of [x - offset, x + offset]) {
          if (cx < EDGE || cx > maxX) continue
          const start = { ...base, x: cx }
          const obstacles = this.occupied.filter((r) => r.x < cx + rect.width + GAP && r.x + r.width + GAP > cx)
          candidates.push(move(start, direction, obstacles), move(start, direction === -1 ? 1 : -1, obstacles))
        }
      }
      const fitting = candidates.filter(inside)
      fitting.sort((a, b) => Math.abs(a.y - rect.y) + Math.abs(a.x - rect.x) * 2
        - Math.abs(b.y - rect.y) - Math.abs(b.x - rect.x) * 2)
      if (fitting.length) best = fitting[0]
      // If the pane is completely full, retain distinct positions rather than overprint text.
    }
    this.occupied.push(best)
    return best
  }
}

function overlaps(a: AnnotationRect, b: AnnotationRect): boolean {
  return a.x < b.x + b.width + GAP && a.x + a.width + GAP > b.x
    && a.y < b.y + b.height + GAP && a.y + a.height + GAP > b.y
}
