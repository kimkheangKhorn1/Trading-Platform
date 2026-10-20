import type {
  BacktestJob,
  BacktestRequest,
  CandlesResponse,
  DownloadJob,
  ExnessHistoryStatus,
  Feed,
  Health,
  ImportReport,
  IndicatorOutput,
  InputValue,
  ScriptMeta,
  TimeframeCode,
  TuningJob,
  TuningRequest,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw new ApiError(0, 'Backend unreachable - is the API server running (default port 8010)?')
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, msg)
  }
  return (await res.json()) as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  startTuning: (req: TuningRequest) => request<{ id: string }>('/api/tuning', json(req)),
  tuning: (id: string) => request<TuningJob>(`/api/tuning/${encodeURIComponent(id)}`),
  stopTuning: (id: string) => request<{ stopping: string }>(`/api/tuning/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  health: () => request<Health>('/api/health'),
  exnessHistory: () => request<ExnessHistoryStatus>('/api/exness/history'),
  feeds: () => request<Feed[]>('/api/feeds'),
  deleteFeed: (id: string) => request<{ deleted: string }>(`/api/feeds/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  candles: (feedId: string, tf: TimeframeCode, opts: { before?: number; after?: number; limit?: number } = {}) => {
    const q = new URLSearchParams({ feed_id: feedId, timeframe: tf, limit: String(opts.limit ?? 2000) })
    if (opts.before !== undefined) q.set('before', String(opts.before))
    if (opts.after !== undefined) q.set('after', String(opts.after))
    return request<CandlesResponse>(`/api/candles?${q}`)
  },
  importCsv: (form: FormData) =>
    request<{ feed: Feed; report: ImportReport }>('/api/import/csv', { method: 'POST', body: form }),
  startDownload: (feedId: string, timeframe: TimeframeCode, until: number) =>
    request<DownloadJob>(`/api/feeds/${encodeURIComponent(feedId)}/download`, json({ timeframe, until })),
  downloads: () => request<DownloadJob[]>('/api/downloads'),
  scripts: () => request<ScriptMeta[]>('/api/scripts'),
  runIndicator: (body: {
    feed_id: string
    timeframe: TimeframeCode
    script_id: string
    inputs: Record<string, InputValue>
    start_time: number
    end_time?: number | null
    replay_start?: number | null
  }) => request<IndicatorOutput>('/api/indicators/run', json(body)),
  submitBacktest: (req: BacktestRequest) => request<{ id: string }>('/api/backtests', json(req)),
  backtest: (id: string) => request<BacktestJob>(`/api/backtests/${id}`),
  backtests: () => request<BacktestJob[]>('/api/backtests'),
}
