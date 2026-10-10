// Display formatting. All data stays in UTC seconds; only rendering converts to the chosen zone.

export type TimezoneId = 'UTC' | 'America/New_York' | 'Europe/London' | 'local'

export const TIMEZONES: { id: TimezoneId; label: string }[] = [
  { id: 'UTC', label: 'UTC' },
  { id: 'America/New_York', label: 'New York' },
  { id: 'Europe/London', label: 'London' },
  { id: 'local', label: 'Local' },
]

const fmtCache = new Map<string, Intl.DateTimeFormat>()

function formatter(tz: TimezoneId, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(opts)
  let f = fmtCache.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat('en-GB', { ...opts, timeZone: tz === 'local' ? undefined : tz, hourCycle: 'h23' })
    fmtCache.set(key, f)
  }
  return f
}

function parts(ts: number, tz: TimezoneId): Record<string, string> {
  const out: Record<string, string> = {}
  for (const p of formatter(tz, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short',
  }).formatToParts(new Date(ts * 1000))) {
    out[p.type] = p.value
  }
  return out
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-03-02 14:05" in the given zone */
export function fmtDateTime(ts: number | null | undefined, tz: TimezoneId): string {
  if (ts === null || ts === undefined) return '—'
  const p = parts(ts, tz)
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`
}

export function fmtDate(ts: number | null | undefined, tz: TimezoneId): string {
  if (ts === null || ts === undefined) return '—'
  const p = parts(ts, tz)
  return `${p.year}-${p.month}-${p.day}`
}

/** Crosshair label, TradingView-like: "Mon 02 Mar '26 14:05" */
export function fmtCrosshair(ts: number, tz: TimezoneId, intraday: boolean): string {
  const p = parts(ts, tz)
  const base = `${p.weekday} ${p.day} ${MONTHS[Number(p.month) - 1]} '${p.year.slice(2)}`
  return intraday ? `${base}  ${p.hour}:${p.minute}` : base
}

/** Axis tick label. tickType follows lightweight-charts' TickMarkType (0 year,1 month,2 day,3 time,4 time+sec) */
export function fmtTick(ts: number, tz: TimezoneId, tickType: number): string {
  const p = parts(ts, tz)
  switch (tickType) {
    case 0:
      return p.year
    case 1:
      return MONTHS[Number(p.month) - 1]
    case 2:
      return String(Number(p.day))
    default:
      return `${p.hour}:${p.minute}`
  }
}

/** Daily FX candles open at 17:00 New York; TradingView labels them with the trading date (next day). */
export function tradingDate(ts: number): number {
  return ts + 7 * 3600
}

export function fmtPrice(v: number | null | undefined, precision: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toFixed(precision)
}

export function fmtMoney(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const s = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return `${v < 0 ? '−' : ''}${s}`
}

export function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${fmtNum(v, digits)}%`
}

/** UTC date string "YYYY-MM-DD" <-> unix seconds at 00:00 UTC */
export function dateToTs(date: string, endOfDay = false): number {
  const t = Date.parse(`${date}T00:00:00Z`) / 1000
  return endOfDay ? t + 86_399 : t
}

export function tsToDate(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 10)
}
