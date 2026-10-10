/** Colors travel as #RRGGBBAA (Pine colors carry transparency). */

export function splitColor(c: string): { hex: string; alpha: number } {
  const s = c.replace('#', '')
  const hex = `#${s.slice(0, 6)}`
  const alpha = s.length >= 8 ? parseInt(s.slice(6, 8), 16) / 255 : 1
  return { hex, alpha }
}

export function joinColor(hex: string, alpha: number): string {
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 255)
  return `${hex.slice(0, 7).toUpperCase()}${a.toString(16).padStart(2, '0').toUpperCase()}`
}

export function withAlpha(c: string, alpha: number): string {
  return joinColor(splitColor(c).hex, alpha)
}

/** Canvas accepts #RRGGBBAA, but rgba() is safest across browsers. */
export function cssColor(c: string): string {
  const s = c.replace('#', '')
  if (s.length < 6) return c
  const r = parseInt(s.slice(0, 2), 16)
  const g = parseInt(s.slice(2, 4), 16)
  const b = parseInt(s.slice(4, 6), 16)
  const a = s.length >= 8 ? parseInt(s.slice(6, 8), 16) / 255 : 1
  return `rgba(${r},${g},${b},${a.toFixed(3)})`
}

export const THEME = {
  bg: '#090d14',
  panel: '#1e222d',
  border: '#2a2e39',
  text: '#d1d4dc',
  muted: '#787b86',
  grid: 'rgba(26, 32, 44, 0.5)',
  up: '#089981',
  down: '#f23645',
  accent: '#2962ff',
  crosshair: '#758696',
}
