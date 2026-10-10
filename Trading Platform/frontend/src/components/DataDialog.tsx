import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DownloadJob, ImportReport, TimeframeCode } from '../api/types'
import { dateToTs, fmtDateTime, tsToDate } from '../lib/format'
import { ALL_TFS, TF_LABELS, useApp } from '../state/app'
import { Select } from './common/Fields'
import { Icon, Spinner } from './common/Icon'
import { Modal } from './common/Modal'

const TZ_OPTIONS = [
  { value: 'UTC', label: 'UTC (Dukascopy, most exports)' },
  { value: 'NY+7', label: 'Broker server time (New York + 7h, MT4/MT5)' },
  { value: 'UTC-5', label: 'UTC−5 fixed (HistData EST)' },
  { value: 'UTC+2', label: 'UTC+2 fixed' },
  { value: 'UTC+3', label: 'UTC+3 fixed' },
  { value: 'America/New_York', label: 'America/New_York (with DST)' },
  { value: 'Europe/London', label: 'Europe/London (with DST)' },
]

export function DataDialog() {
  const close = () => useApp.getState().openDialog(null)
  const feeds = useApp((s) => s.feeds)
  const health = useApp((s) => s.health)
  const tz = useApp((s) => s.timezone)
  const refresh = useApp((s) => s.refreshFeeds)
  const [confirm, setConfirm] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<ImportReport | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [form, setForm] = useState({ symbol: 'XAUUSD', dataset: '', price_type: 'bid', timezone: 'UTC', timeframe: '', alignment: 'ny17' })
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [dl, setDl] = useState<{ tf: TimeframeCode; until: string }>(() => ({ tf: 'M1', until: tsToDate(Date.now() / 1000 - 365 * 86400) }))
  const oanda = feeds.find((f) => f.provider === 'oanda')

  useEffect(() => {
    let stop = false
    const poll = async () => {
      try {
        const list = await api.downloads()
        if (stop) return
        setJobs(list)
        if (list.some((j) => j.status === 'running')) setTimeout(poll, 1500)
        else void refresh()
      } catch {
        /* backend unavailable: shown elsewhere */
      }
    }
    void poll()
    return () => {
      stop = true
    }
  }, [refresh])

  const doImport = async () => {
    if (!file) return
    setBusy(true)
    setError(null)
    setReport(null)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('symbol', form.symbol)
    fd.append('dataset', form.dataset || file.name.replace(/\.[^.]+$/, ''))
    fd.append('price_type', form.price_type)
    fd.append('timezone', form.timezone)
    fd.append('timeframe', form.timeframe)
    fd.append('alignment', form.alignment)
    try {
      const res = await api.importCsv(fd)
      setReport(res.report)
      await refresh()
      useApp.getState().setFeed(res.feed.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async (id: string) => {
    setBusy(true)
    try {
      await api.deleteFeed(id)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      setConfirm(null)
    }
  }

  const startDownload = async () => {
    if (!oanda) return
    setError(null)
    try {
      const job = await api.startDownload(oanda.id, dl.tf, dateToTs(dl.until))
      setJobs([job, ...jobs])
      setTimeout(async () => setJobs(await api.downloads()), 1500)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Modal title="Data manager" onClose={close} width={860} className="data-dialog">
      {error ? <div className="alert error"><Icon name="warning" size={15} /> {error}</div> : null}
      <section>
        <h4 className="group-title">Stored feeds</h4>
        {feeds.length === 0 ? <p className="muted">Nothing stored yet.</p> : null}
        {feeds.map((f) => (
          <div key={f.id} className="feed-card">
            <div className="feed-head">
              <span className="sym">{f.symbol}</span>
              <span className={`src-badge ${f.provider}`}>{f.provider.toUpperCase()}</span>
              <span className="muted">{f.display_name}</span>
              <span className="muted small">· {f.price_type} · {f.precision} decimals · {f.alignment === 'ny17' ? 'NY 17:00 sessions' : 'UTC sessions'}</span>
              <div className="grow" />
              {confirm === f.id ? (
                <>
                  <span className="small warn-text">Delete all stored candles of this feed?</span>
                  <button className="btn danger sm" disabled={busy} onClick={() => doDelete(f.id)}>Delete</button>
                  <button className="btn ghost sm" onClick={() => setConfirm(null)}>Keep</button>
                </>
              ) : (
                <button className="icon-btn" title="Delete feed" onClick={() => setConfirm(f.id)}><Icon name="trash" size={16} /></button>
              )}
            </div>
            <table className="mini-table">
              <thead><tr><th>TF</th><th>Origin</th><th className="num">Candles</th><th>First</th><th>Last</th><th>History</th></tr></thead>
              <tbody>
                {f.series.map((s) => (
                  <tr key={s.timeframe}>
                    <td>{TF_LABELS[s.timeframe]}</td>
                    <td className="muted">{s.origin}</td>
                    <td className="num mono">{s.count.toLocaleString('en-US')}</td>
                    <td className="mono">{fmtDateTime(s.first, tz)}</td>
                    <td className="mono">{fmtDateTime(s.last, tz)}</td>
                    <td className="muted small">{s.oldest_reached ? 'complete' : f.provider === 'oanda' ? 'more available on scroll' : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </section>

      <section>
        <h4 className="group-title">OANDA</h4>
        {health?.oanda.enabled ? (
          <>
            <p className="muted small">
              Connected to the <b>{health.oanda.environment}</b> environment ({health.oanda.instruments.join(', ')}, price component {health.oanda.price_component}).
              Candles are cached in the local database; scrolling back loads older history on demand.
              {health.oanda.last_error ? <span className="warn-text"> Last error: {health.oanda.last_error}</span> : null}
            </p>
            {oanda ? (
              <div className="form-row">
                <span>Download</span>
                <Select value={dl.tf} options={ALL_TFS.map((t) => ({ value: t, label: TF_LABELS[t] }))} onChange={(tf) => setDl({ ...dl, tf })} />
                <span>history back to</span>
                <input className="input" type="date" value={dl.until} onChange={(e) => setDl({ ...dl, until: e.target.value })} />
                <button className="btn primary sm" onClick={startDownload}>Start</button>
              </div>
            ) : null}
            {jobs.map((j) => (
              <div key={j.id} className="job-row small">
                {j.status === 'running' ? <Spinner size={11} /> : j.status === 'done' ? <Icon name="check" size={14} /> : <Icon name="warning" size={14} />}
                <span>{TF_LABELS[j.timeframe as TimeframeCode] ?? j.timeframe} → {tsToDate(j.until)}</span>
                <span className="muted">{j.added.toLocaleString('en-US')} candles added{j.oldest ? `, reached ${fmtDateTime(j.oldest, tz)}` : ''}</span>
                {j.error ? <span className="warn-text">{j.error}</span> : null}
              </div>
            ))}
          </>
        ) : (
          <p className="muted small">
            Not configured. Put <code>OANDA_API_TOKEN</code> (and optionally <code>OANDA_ACCOUNT_ID</code>,
            <code> OANDA_ENVIRONMENT=practice|live</code>) in <code>backend/.env</code> and restart the backend to enable
            historical downloads and live candles. Until then, import CSV data below.
          </p>
        )}
      </section>

      <section>
        <h4 className="group-title">Import CSV</h4>
        <div className="import-grid">
          <label className="file-drop">
            <Icon name="upload" size={18} />
            <span>{file ? `${file.name} (${(file.size / 1e6).toFixed(1)} MB)` : 'Choose a CSV file…'}</span>
            <input type="file" accept=".csv,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label>Symbol<input className="input" value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} /></label>
          <label>Dataset name<input className="input" placeholder={file?.name.replace(/\.[^.]+$/, '') ?? 'e.g. dukascopy'} value={form.dataset}
            onChange={(e) => setForm({ ...form, dataset: e.target.value })} /></label>
          <label>Prices are
            <Select value={form.price_type} options={[{ value: 'bid', label: 'Bid' }, { value: 'mid', label: 'Mid' }, { value: 'ask', label: 'Ask' }]}
              onChange={(v) => setForm({ ...form, price_type: v })} />
          </label>
          <label>Timestamps in
            <Select value={form.timezone} options={TZ_OPTIONS} onChange={(v) => setForm({ ...form, timezone: v })} />
          </label>
          <label>Timeframe
            <Select value={form.timeframe} options={[{ value: '', label: 'Auto-detect' }, ...ALL_TFS.map((t) => ({ value: t, label: TF_LABELS[t] }))]}
              onChange={(v) => setForm({ ...form, timeframe: v })} />
          </label>
          <label>Higher-timeframe sessions
            <Select value={form.alignment} options={[{ value: 'ny17', label: '17:00 New York (OANDA/TradingView FX)' }, { value: 'utc', label: '00:00 UTC' }]}
              onChange={(v) => setForm({ ...form, alignment: v })} />
          </label>
          <div className="import-actions">
            <button className="btn primary" disabled={!file || busy} onClick={doImport}>{busy ? <Spinner size={12} /> : null} Import</button>
          </div>
        </div>
        <p className="muted small">
          Columns are detected automatically (time or date+time, open, high, low, close, optional volume). Rows with
          inconsistent OHLC values are rejected and reported, never repaired. Higher timeframes are built from the
          imported candles.
        </p>
        {report ? (
          <div className="alert ok">
            <Icon name="check" size={15} /> Imported {report.rows_imported.toLocaleString('en-US')} {report.timeframe} candles
            ({fmtDateTime(report.first_time, tz)} → {fmtDateTime(report.last_time, tz)}) from {report.rows_read.toLocaleString('en-US')} rows.
            {report.warnings.map((w) => <div key={w} className="warn-text">{w}</div>)}
          </div>
        ) : null}
      </section>
    </Modal>
  )
}

export function SymbolInfoDialog() {
  const close = () => useApp.getState().openDialog(null)
  const feeds = useApp((s) => s.feeds)
  const feedId = useApp((s) => s.feedId)
  const tz = useApp((s) => s.timezone)
  const f = feeds.find((x) => x.id === feedId)
  if (!f) return null
  const rows: [string, string][] = [
    ['Symbol', f.symbol],
    ['Description', f.symbol === 'XAUUSD' ? 'Gold spot / U.S. dollar' : f.symbol],
    ['Source', f.provider === 'oanda' ? `OANDA v20 (${f.instrument})` : `CSV import (${(f.meta.files as string[] | undefined)?.filter(Boolean).join(', ') ?? 'file'})`],
    ['Price type', `${f.price_type} prices`],
    ['Price precision', `${f.precision} decimals (tick ${(10 ** -f.precision).toFixed(f.precision)})`],
    ['Session boundaries', f.alignment === 'ny17' ? '17:00 America/New_York (daily and 4H candles)' : '00:00 UTC'],
    ['Live updates', f.live ? 'yes (polling OANDA candles)' : 'no'],
    ['Stored candles', f.series.map((s) => `${TF_LABELS[s.timeframe]} ${s.count.toLocaleString('en-US')}`).join(' · ')],
    ['Stored range', f.series.length ? `${fmtDateTime(Math.min(...f.series.map((s) => s.first ?? Infinity)), tz)} → ${fmtDateTime(Math.max(...f.series.map((s) => s.last ?? 0)), tz)}` : '—'],
  ]
  return (
    <Modal title="Symbol info" onClose={close} width={560}>
      <table className="kv-table">
        <tbody>{rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody>
      </table>
      <p className="muted small note">
        Prices differ between providers (OANDA mid vs a broker's bid, different liquidity sources), so signals can
        differ slightly from a TradingView chart that uses another feed.
      </p>
    </Modal>
  )
}

export function GotoDialog() {
  const close = () => useApp.getState().openDialog(null)
  const focusRange = useApp((s) => s.focusRange)
  const [date, setDate] = useState(() => tsToDate(Date.now() / 1000 - 7 * 86400))
  const [time, setTime] = useState('12:00')
  const go = () => {
    const ts = dateToTs(date) + Number(time.slice(0, 2)) * 3600 + Number(time.slice(3, 5)) * 60
    focusRange(ts, ts)
    close()
  }
  return (
    <Modal title="Go to" onClose={close} width={380}
      footer={<><div className="grow" /><button className="btn ghost" onClick={close}>Cancel</button><button className="btn primary" onClick={go}>Go to</button></>}>
      <div className="form-row">
        <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Date (UTC)" />
        <input className="input" type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Time (UTC)" />
        <span className="muted small">UTC</span>
      </div>
      <p className="muted small">Older history is loaded automatically if needed.</p>
    </Modal>
  )
}
