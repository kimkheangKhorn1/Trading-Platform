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

  /** Mark space as taken without searching, e.g. for rects already placed by packRow. */
  reserve(rect: AnnotationRect) {
    this.occupied.push(rect)
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

/**
 * Lay out price badges that point sideways at a level. Each keeps its price; badges whose rows collide are lined
 * up left to right in order of their own x. A run that would leave the pane on the right slides left as a group.
 * Returns null for a badge that still can't fit in its row, so the caller can fall back to the vertical search.
 */
export function packRow(rects: AnnotationRect[], width: number, height: number): (AnnotationRect | null)[] {
  const out = rects.map((r) => ({ ...r, x: Math.max(EDGE, r.x),
    y: Math.max(EDGE, Math.min(height - EDGE - r.height, r.y)) }))
  const sameRow = (a: AnnotationRect, b: AnnotationRect) => a.y < b.y + b.height + GAP && a.y + a.height + GAP > b.y
  const order = out.map((_, i) => i).sort((i, j) => out[i].x - out[j].x || out[i].y - out[j].y)
  const done: number[] = []
  for (const i of order) {
    for (const j of done) if (sameRow(out[i], out[j])) out[i].x = Math.max(out[i].x, out[j].x + out[j].width + GAP)
    done.push(i)
  }
  // Badges linked by shared rows move together, so sliding a group can't run into a badge outside it.
  const group = out.map((_, i) => i)
  const root = (i: number): number => (group[i] === i ? i : (group[i] = root(group[i])))
  for (let i = 0; i < out.length; i++) for (let j = i + 1; j < out.length; j++)
    if (sameRow(out[i], out[j])) group[root(i)] = root(j)
  const members = new Map<number, number[]>()
  out.forEach((_, i) => members.set(root(i), [...(members.get(root(i)) ?? []), i]))
  for (const ids of members.values()) {
    const over = Math.max(...ids.map((i) => out[i].x + out[i].width)) - (width - EDGE)
    const shift = Math.min(over, Math.min(...ids.map((i) => out[i].x)) - EDGE)
    if (shift > 0) for (const i of ids) out[i].x -= shift
  }
  return out.map((r) => (r.x + r.width <= width - EDGE ? r : null))
}

function overlaps(a: AnnotationRect, b: AnnotationRect): boolean {
  return a.x < b.x + b.width + GAP && a.x + a.width + GAP > b.x
    && a.y < b.y + b.height + GAP && a.y + a.height + GAP > b.y
}
