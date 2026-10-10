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

/** HSV (h 0-360, s/v 0-1) <-> #RRGGBB, for the custom color picker. */
export function hexToHsv(hex: string): { h: number; s: number; v: number } {
  const s = hex.replace('#', '')
  const r = parseInt(s.slice(0, 2), 16) / 255
  const g = parseInt(s.slice(2, 4), 16) / 255
  const b = parseInt(s.slice(4, 6), 16) / 255
  const max = Math.max(r, g, b)
  const d = max - Math.min(r, g, b)
  let h = 0
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return { h: (h * 60 + 360) % 360, s: max ? d / max : 0, v: max }
}

export function hsvToHex(h: number, s: number, v: number): string {
  const f = (n: number) => {
    const k = (n + h / 60) % 6
    return Math.round((v - v * s * Math.max(0, Math.min(k, 4 - k, 1))) * 255).toString(16).padStart(2, '0')
  }
  return `#${f(5)}${f(3)}${f(1)}`.toUpperCase()
}
