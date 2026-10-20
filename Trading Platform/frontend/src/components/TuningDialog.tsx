import { useMemo, useState } from 'react'
import type { InputSpec, InputValue, ScriptMeta, TuneResult, TuningJob, TuningObjective, TuningParamSpec } from '../api/types'
import { load, save } from '../lib/storage'
import { currentFeed, TF_LABELS, TF_SECONDS, useApp } from '../state/app'
import { useIndicators } from '../state/indicators'
import { useTuning } from '../state/tuning'
import { Icon, Spinner } from './common/Icon'
import { Modal } from './common/Modal'
import { Select } from './common/Select'

/** One tunable setting: tuned or not, and its range (numbers) or the options to try. */
interface Row {
  id: string
  on: boolean
  min: number
  max: number
  step: number
  options: InputValue[] | null
  chosen: InputValue[]
}

interface Setup {
  rows: Row[]
  days: number
  start: number | null
  end: number | null
  minTrades: number
  tunes: number
  objective: TuningObjective
}

const OBJECTIVE_LABEL: Record<TuningObjective, string> = {
  rule: 'TP / SL rule',
  split: 'Real P&L · ⅓ at each TP',
  hold: 'Real P&L · hold to TP3',
}
const PERIODS = [7, 30, 90, 180, 365]
const MAX_TUNES = 10000

const toLocal = (ts: number) => new Date(ts * 1000).toISOString().slice(0, 16)
const fromLocal = (v: string) => Math.floor(Date.parse(`${v}Z`) / 1000)
const fmtR = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(Number.isInteger(v) ? 0 : 1)}R`
const fmtBig = (v: number) => (v >= 1e12 ? v.toExponential(2).replace('e+', ' × 10^') : Math.round(v).toLocaleString('en-US'))
const fmtShare = (f: number) => (f >= 0.0001 ? `${(f * 100).toPrecision(2)}%` : 'less than 0.01%')
const fmtVal = (v: InputValue) => (typeof v === 'boolean' ? (v ? 'On' : 'Off') : String(v))

function rowCount(r: Row): number {
  if (!r.on) return 1
  if (r.options) return r.chosen.length
  if (!(r.step > 0) || r.max < r.min) return 0
  return Math.floor((r.max - r.min) / r.step + 1e-9) + 1
}

function defaultRows(spec: TuningParamSpec[]): Row[] {
  return spec.map((p) => ({
    id: p.id, on: !!p.on, min: p.min ?? 0, max: p.max ?? 0, step: p.step ?? 1,
    options: p.options ?? null, chosen: p.options ? [...p.options] : [],
  }))
}

/** Saved setup of a script, with rows added / removed since it was saved brought in line with the script. */
function loadSetup(meta: ScriptMeta): Setup {
  const spec = meta.tuning!.params
  const saved = load<Partial<Setup>>(`tp.tuning.${meta.id}`, {})
  const rows = defaultRows(spec).map((d) => ({ ...d, ...(saved.rows?.find((r) => r.id === d.id) ?? {}), options: d.options }))
  const setup: Setup = { days: 30, start: null, end: null, minTrades: 30, tunes: 200, objective: 'rule', ...saved, rows }
  // a value saved by the later overnight tuner (e.g. "All") is brought back into this tuner's range
  setup.tunes = Math.min(MAX_TUNES, Math.max(1, Math.round(setup.tunes) || 200))
  if (!(setup.objective in OBJECTIVE_LABEL)) setup.objective = 'rule'
  return setup
}

export function TuningDialog({ uid }: { uid: string }) {
  const close = useApp((s) => s.openDialog)
  const scripts = useApp((s) => s.scripts)
  const tf = useApp((s) => s.timeframe)
  const inst = useIndicators((s) => s.instances.find((i) => i.uid === uid))
  const meta = scripts.find((s) => s.id === inst?.scriptId)
  const tuning = useTuning()
  const feed = currentFeed()
  const [setup, setSetup] = useState<Setup | null>(() => (meta?.tuning ? loadSetup(meta) : null))

  if (!inst || !meta?.tuning || !setup) {
    return <Modal title="Tuning" onClose={() => close(null)}><p className="pad muted">This indicator cannot be tuned.</p></Modal>
  }
  const update = (patch: Partial<Setup>) => {
    const next = { ...setup, ...patch }
    setSetup(next)
    save(`tp.tuning.${meta.id}`, next)
  }
  const job = tuning.uid === uid ? tuning.job : null
  const running = job?.status === 'running' || (tuning.uid === uid && tuning.starting)

  return (
    <Modal className="tune-modal" width={1120} onClose={() => close(null)}
      title={<span className="tune-title"><Icon name="flask" size={16} /> Tune · {meta.short_name}
        {job ? <StatusChip job={job} /> : null}</span>}>
      <div className="tune-grid">
        <SetupPane meta={meta} setup={setup} update={update} running={!!running} feedName={feed?.symbol ?? '—'}
          tfLabel={TF_LABELS[tf]} lastBar={feed?.series.find((s) => s.timeframe === tf)?.last ?? null}
          firstBar={feed?.series.find((s) => s.timeframe === tf)?.first ?? null}
          onStart={(start, end) => {
            if (!feed) return
            tuning.start(uid, {
              feed_id: feed.id, timeframe: tf, script_id: meta.id, inputs: inst.inputs, start, end,
              min_trades: setup.minTrades, tunes: setup.tunes, objective: setup.objective,
              ranges: setup.rows.filter((r) => r.on).map((r) => (r.options ? { id: r.id, options: r.chosen }
                : { id: r.id, min: r.min, max: r.max, step: r.step })),
            })
          }}
          onStop={() => tuning.stop()} />
        <LivePane meta={meta} job={job} error={tuning.uid === uid ? tuning.error : null} starting={tuning.starting} />
      </div>
    </Modal>
  )
}

function StatusChip({ job }: { job: TuningJob }) {
  const label = { running: 'Tuning…', done: 'Finished', stopped: 'Stopped', error: 'Failed' }[job.status]
  return <span className={`tune-chip ${job.status}`}>{job.status === 'running' ? <Spinner size={10} /> : null}{label}</span>
}

// ------------------------------------------------------------------------------------------------------------ setup
function SetupPane({ meta, setup, update, running, feedName, tfLabel, firstBar, lastBar, onStart, onStop }: {
  meta: ScriptMeta; setup: Setup; update: (p: Partial<Setup>) => void; running: boolean; feedName: string
  tfLabel: string; firstBar: number | null; lastBar: number | null
  onStart: (start: number, end: number) => void; onStop: () => void
}) {
  const tf = useApp((s) => s.timeframe)
  const [now] = useState(() => Math.floor(Date.now() / 1000))
  const end = setup.end ?? lastBar ?? now
  const start = setup.start ?? end - setup.days * 86400
  const specs = useMemo(() => new Map(meta.inputs.map((i) => [i.id, i])), [meta])
  const counts = setup.rows.map(rowCount)
  const tuned = setup.rows.filter((r) => r.on)
  const total = setup.rows.reduce((t, r, k) => (r.on ? t * counts[k] : t), 1)
  const candles = Math.max(0, Math.round(((end - start) / TF_SECONDS[tf]) * (5 / 7)))
  const problems = [
    !tuned.length && 'Tick at least one setting to tune.',
    setup.rows.some((r, k) => r.on && counts[k] < 1) && 'A ticked setting has no values (check min ≤ max and the step).',
    end <= start && 'The end time must be after the start time.',
    !(setup.tunes >= 1 && setup.tunes <= MAX_TUNES) && `Number of tunes must be 1 – ${MAX_TUNES}.`,
  ].filter(Boolean) as string[]
  const setRow = (id: string, patch: Partial<Row>) => update({ rows: setup.rows.map((r) => (r.id === id ? { ...r, ...patch } : r)) })
  const tested = Math.min(setup.tunes, total + 1)

  return (
    <section className="tune-setup">
      <div className="tune-card">
        <h4><Icon name="calendar" size={14} /> Tuning period <span className="muted">· {feedName} · {tfLabel} · UTC</span></h4>
        <div className="tune-period">
          <label>Start<input type="datetime-local" className="input" value={toLocal(start)} disabled={running}
            min={firstBar ? toLocal(firstBar) : undefined} max={toLocal(end)}
            onChange={(e) => e.target.value && update({ start: fromLocal(e.target.value) })} /></label>
          <span className="tune-arrow">→</span>
          <label>End<input type="datetime-local" className="input" value={toLocal(end)} disabled={running}
            max={lastBar ? toLocal(lastBar) : undefined}
            onChange={(e) => e.target.value && update({ end: fromLocal(e.target.value) })} /></label>
        </div>
        <div className="tune-quick">
          {PERIODS.map((d) => (
            <button key={d} className={`tune-pill ${setup.start === null && setup.end === null && setup.days === d ? 'on' : ''}`}
              disabled={running} onClick={() => update({ days: d, start: null, end: null })}>
              {d >= 365 ? '1Y' : `${d}D`}
            </button>
          ))}
          <span className="muted small">≈ {candles.toLocaleString('en-US')} candles to the latest data</span>
        </div>
      </div>

      <div className="tune-card tune-row3">
        <label title="A tune with fewer finished trades than this cannot win - too few trades is mostly luck.">
          <span>Min trades</span>
          <input type="number" className="input" min={0} value={setup.minTrades} disabled={running}
            onChange={(e) => update({ minTrades: Math.max(0, Number(e.target.value)) })} />
        </label>
        <label title="How many combinations the search tests (tune #1 is your current settings).">
          <span>Tunes to run</span>
          <input type="number" className="input" min={1} max={MAX_TUNES} value={setup.tunes} disabled={running}
            onChange={(e) => update({ tunes: Number(e.target.value) })} />
        </label>
        <div className="tune-field wide" title="What 'best' means.">
          <span>Best by</span>
          {running
            ? <div className="input tune-static">{OBJECTIVE_LABEL[setup.objective]}</div>
            : <Select ariaLabel="Best by" value={setup.objective} onChange={(v) => update({ objective: v })}
              options={(Object.keys(OBJECTIVE_LABEL) as TuningObjective[]).map((o) => ({ value: o, label: OBJECTIVE_LABEL[o] }))} />}
        </div>
      </div>
      <p className="tune-note">
        {setup.objective === 'rule' ? <><b>TP / SL rule:</b> {meta.tuning!.score} </> : null}
        {setup.objective === 'split' ? <><b>Real P&L, ⅓ at each TP:</b> what a trader closing a third at TP1 / TP2 / TP3 would make. </> : null}
        {setup.objective === 'hold' ? <><b>Real P&L, hold to TP3:</b> the whole position to TP3 or the stop. </> : null}
        {meta.tuning!.note}
      </p>

      <div className="tune-card tune-params">
        <div className="tune-params-head">
          <h4><Icon name="gear" size={14} /> Settings to tune</h4>
          <span className="muted small">{tuned.length} of {setup.rows.length} ticked</span>
        </div>
        <div className="tune-param-list">
          {setup.rows.map((r, k) => {
            const spec = specs.get(r.id)
            if (!spec) return null
            const dupTitle = setup.rows.some((o) => o.id !== r.id && specs.get(o.id)?.title === spec.title)
            return <ParamRow key={r.id} row={r} spec={spec} count={counts[k]} disabled={running} dupTitle={dupTitle}
              onChange={(patch) => setRow(r.id, patch)} />
          })}
        </div>
      </div>

      <div className="tune-space">
        <div className="tune-space-formula">
          {tuned.length ? tuned.map((r) => rowCount(r)).join(' × ') : '—'}
        </div>
        <div className="tune-space-total"><b>{fmtBig(total)}</b> possible combinations</div>
        <div className="muted small">
          {total + 1 <= setup.tunes
            ? `Every combination will be tested (${fmtBig(total)}); tune #1 is your current settings.`
            : `Smart search tests ${tested.toLocaleString('en-US')} of them (${fmtShare(tested / total)}): `
              + 'explores at random first, then refines around the best tunes.'}
        </div>
      </div>

      {problems.length ? <div className="tune-problems">{problems.map((p) => <div key={p}><Icon name="warning" size={13} /> {p}</div>)}</div> : null}
      <div className="tune-actions">
        {running
          ? <button className="btn danger" onClick={onStop}>Stop tuning</button>
          : <button className="btn primary tune-go" disabled={!!problems.length} onClick={() => onStart(start, end)}>
            <Icon name="play" size={14} /> Start tuning</button>}
        <button className="btn ghost" disabled={running} onClick={() => update({ rows: defaultRows(meta.tuning!.params) })}>Reset ranges</button>
      </div>
    </section>
  )
}

function ParamRow({ row, spec, count, disabled, dupTitle, onChange }: {
  row: Row; spec: InputSpec; count: number; disabled: boolean; dupTitle: boolean; onChange: (p: Partial<Row>) => void
}) {
  const num = (k: 'min' | 'max' | 'step', v: string) => onChange({ [k]: Number(v) })
  return (
    <div className={`tune-param ${row.on ? 'on' : ''}`} title={spec.help ?? undefined}>
      <label className="tune-param-name">
        <input type="checkbox" checked={row.on} disabled={disabled} onChange={(e) => onChange({ on: e.target.checked })} />
        <span>{spec.title}{dupTitle && spec.group ? <em className="muted"> · {spec.group.replace(/^[^A-Za-z]+/, '')}</em> : null}</span>
      </label>
      {row.options ? (
        <div className="tune-options">
          {row.options.map((o) => {
            const on = row.chosen.includes(o)
            return <button key={String(o)} className={`tune-pill ${on ? 'on' : ''}`} disabled={disabled || !row.on}
              onClick={() => onChange({ chosen: on ? row.chosen.filter((c) => c !== o) : [...row.chosen, o] })}>{fmtVal(o)}</button>
          })}
        </div>
      ) : (
        <div className="tune-range">
          <input type="number" className="input" aria-label={`${spec.title} from`} value={row.min} step={row.step}
            min={spec.minval ?? undefined} max={spec.maxval ?? undefined} disabled={disabled || !row.on}
            onChange={(e) => num('min', e.target.value)} />
          <span>to</span>
          <input type="number" className="input" aria-label={`${spec.title} to`} value={row.max} step={row.step}
            min={spec.minval ?? undefined} max={spec.maxval ?? undefined} disabled={disabled || !row.on}
            onChange={(e) => num('max', e.target.value)} />
          <span>step</span>
          <input type="number" className="input" aria-label={`${spec.title} step`} value={row.step}
            min={spec.type === 'int' ? 1 : 0.0001} step={spec.type === 'int' ? 1 : 0.05} disabled={disabled || !row.on}
            onChange={(e) => num('step', e.target.value)} />
        </div>
      )}
      <span className={`tune-count ${row.on ? '' : 'muted'}`}>{row.on ? `${count}` : '—'}</span>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------------------ live
function LivePane({ meta, job, error, starting }: { meta: ScriptMeta; job: TuningJob | null; error: string | null; starting: boolean }) {
  const { applied, keptCurrent, undo, apply } = useTuning()
  if (!job) {
    return (
      <section className="tune-live empty">
        {starting ? <><Spinner size={18} /><p>Loading candles and starting the workers…</p></>
          : <>
            <Icon name="target" size={34} />
            <h3>Find the best settings</h3>
            <p>Pick a period, tick the settings to tune and give each a range. Tune #1 is always your current settings;
              every tune that beats the best so far is announced here. When the run ends, the winner is written into
              the indicator's settings — you can undo it.</p>
            <ul>
              <li>One trade at a time — a trade ends at TP3 or the stop.</li>
              <li>Results are on past data, without spread or commission.</li>
            </ul>
          </>}
        {error ? <div className="tune-problems"><Icon name="warning" size={13} /> {error}</div> : null}
      </section>
    )
  }
  const title = (id: string) => meta.inputs.find((i) => i.id === id)?.title ?? id
  const obj = job.objective
  const best = job.best
  const pct = Math.min(100, (job.done / job.budget) * 100)
  const changed = best ? Object.entries(best.params).filter(([k, v]) => job.base[k] !== v) : []

  return (
    <section className="tune-live">
      <div className="tune-progress">
        <div className="tune-progress-top">
          <b>Tune {job.done.toLocaleString('en-US')}</b><span className="muted"> / {job.budget.toLocaleString('en-US')}</span>
          <span className="tune-phase">{job.status === 'running' ? phaseLabel(job.phase) : job.status}</span>
          <span className="muted small grow-right">{job.bars.toLocaleString('en-US')} candles · {job.warmup.toLocaleString('en-US')} warm-up · {fmtBig(job.total_space_float)} combinations</span>
        </div>
        <div className="tune-bar"><div style={{ width: `${pct}%` }} /></div>
      </div>

      {applied && job.status !== 'running' ? (
        <div className="tune-banner ok">
          <Icon name="check" size={16} /> Tune #{applied.n} is now your setting.
          <button className="btn sm ghost" onClick={undo}>Undo</button>
        </div>
      ) : null}
      {keptCurrent && job.status !== 'running' ? (
        <div className="tune-banner">No tune beat your current settings (tune #1) - nothing changed.</div>
      ) : null}
      {job.status !== 'running' && best && !best.qualified ? (
        <div className="tune-banner warn">No tune reached {job.request.min_trades} trades - nothing applied. Lower the minimum or use a longer period.</div>
      ) : null}
      {job.status === 'error' ? <div className="tune-banner warn">{job.error}</div> : null}

      {best ? (
        <div className={`tune-best ${best.qualified ? '' : 'unq'}`}>
          <div className="tune-best-head">
            <span className="tune-trophy">🏆</span>
            <div>
              <div className="tune-best-title">Tune #{best.n} {best.n === 1 ? '(your current settings)' : ''} is the best{job.status === 'running' ? ' so far' : ''}</div>
              <div className="muted small">{best.qualified ? OBJECTIVE_LABEL[obj] : `below ${job.request.min_trades} trades - cannot win yet`}</div>
            </div>
            <div className={`tune-score ${best.metrics[obj] >= 0 ? 'up' : 'down'}`}>{fmtR(best.metrics[obj])}</div>
          </div>
          <Metrics m={best.metrics} obj={obj} vs={job.baseline && job.baseline.n !== best.n ? job.baseline : null} />
          {changed.length ? (
            <div className="tune-changes">
              {changed.map(([k, v]) => <span key={k} className="tune-change">{title(k)} <s>{fmtVal(job.base[k])}</s> → <b>{fmtVal(v)}</b></span>)}
            </div>
          ) : null}
        </div>
      ) : null}

      <ScoreChart job={job} />

      <div className="tune-two">
        <div className="tune-card">
          <h4>Best so far</h4>
          <div className="tune-events">
            {job.events.length ? job.events.map((e) => (
              <div key={e.n} className="tune-event">
                <span className="tune-event-n">#{e.n}</span>
                {e.prev === null ? <span>first qualified tune · {fmtR(e.score)}</span>
                  : e.score === e.prev_score
                    ? <span>beats #{e.prev} · same {fmtR(e.score)}, higher win rate</span>
                    : <span>beats #{e.prev} · {fmtR(e.prev_score!)} → <b className={e.score >= 0 ? 'up' : 'down'}>{fmtR(e.score)}</b></span>}
              </div>
            )) : <div className="muted small">No tune with {job.request.min_trades}+ trades yet.</div>}
          </div>
        </div>
        <div className="tune-card">
          <h4>Leaderboard</h4>
          <table className="tune-table">
            <thead><tr><th>#</th><th>Score</th><th>Trades</th><th>W / L</th><th>Win</th><th /></tr></thead>
            <tbody>
              {job.top.slice(0, 8).map((r) => (
                <tr key={r.n} className={`${r.n === best?.n ? 'best' : ''} ${r.qualified ? '' : 'unq'}`} title={paramText(r, title)}>
                  <td>{r.n}</td>
                  <td className={r.metrics[obj] >= 0 ? 'up' : 'down'}>{fmtR(r.metrics[obj])}</td>
                  <td>{r.metrics.trades}</td>
                  <td>{r.metrics.wins} / {r.metrics.losses}</td>
                  <td>{(r.metrics.win_rate * 100).toFixed(0)}%</td>
                  <td>{job.status !== 'running' && r.qualified && applied?.n !== r.n
                    ? <button className="btn sm ghost" onClick={() => apply(r.n)}>Use</button> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

const phaseLabel = (p: string) => (p === 'explore' ? 'exploring' : p === 'refine' ? 'refining around the best' : p)
const paramText = (r: TuneResult, title: (id: string) => string) =>
  Object.entries(r.params).map(([k, v]) => `${title(k)}: ${fmtVal(v)}`).join('\n')

function Metrics({ m, obj, vs }: { m: TuneResult['metrics']; obj: TuningObjective; vs: TuneResult | null }) {
  const cell = (label: string, value: string, tip?: string, cls?: string) => (
    <div className="tune-metric" title={tip}><span>{label}</span><b className={cls}>{value}</b></div>
  )
  const realKey: TuningObjective = obj === 'hold' ? 'split' : 'hold'
  return (
    <div className="tune-metrics">
      {cell('Trades', String(m.trades))}
      {cell('Wins (TP3)', String(m.wins), undefined, 'up')}
      {cell('Losses (SL)', String(m.losses), undefined, 'down')}
      {cell('Win rate', `${(m.win_rate * 100).toFixed(1)}%`)}
      {cell('TP1 / TP2 / TP3', `${m.tp1} / ${m.tp2} / ${m.tp3}`, 'Trades that reached each target')}
      {cell(obj === 'rule' ? 'Real P&L (hold)' : `Also: ${OBJECTIVE_LABEL[realKey]}`, fmtR(m[realKey]),
        'The same trades scored as real money. The TP / SL rule counts each TP as +1R even when the trade later stops out.',
        m[realKey] >= 0 ? 'up' : 'down')}
      {cell('1st half / 2nd half', `${fmtR(m[`${obj}_h1`])} / ${fmtR(m[`${obj}_h2`])}`,
        'Score in each half of the period. A tune that only works in one half is probably luck.')}
      {vs ? cell('Your settings (#1)', fmtR(vs.metrics[obj]), `Tune #1: ${vs.metrics.trades} trades`) : null}
    </div>
  )
}

/** Every tune's score (x = tune number); the line is the best so far, the gold dot the winner. */
function ScoreChart({ job }: { job: TuningJob }) {
  const W = 520
  const H = 130
  const pad = 26
  const pts = job.points
  if (pts.length < 2) return null
  const ys = pts.map((p) => p[1])
  let lo = Math.min(0, ...ys)
  let hi = Math.max(0, ...ys)
  if (hi === lo) hi = lo + 1
  const span = hi - lo
  lo -= span * 0.08
  hi += span * 0.08
  const x = (n: number) => pad + ((n - 1) / Math.max(1, job.budget - 1)) * (W - pad - 6)
  const y = (v: number) => 6 + (1 - (v - lo) / (hi - lo)) * (H - 20)
  const sorted = [...pts].sort((a, b) => a[0] - b[0])
  let run: number | null = null
  const line: string[] = []
  for (const [n, v, q] of sorted) {
    if (!q) continue
    if (run === null || v > run) {
      if (run !== null) line.push(`L${x(n)},${y(run)}`)
      run = v
      line.push(`${line.length ? 'L' : 'M'}${x(n)},${y(v)}`)
    }
  }
  if (run !== null) line.push(`L${x(sorted[sorted.length - 1][0])},${y(run)}`)
  const best = job.best
  return (
    <div className="tune-card tune-chart">
      <h4>Score of every tune <span className="muted small">· hollow = below the minimum trades</span></h4>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Score of every tune">
        <line x1={pad} x2={W - 6} y1={y(0)} y2={y(0)} className="tune-zero" />
        <text x={pad - 4} y={y(0) + 3} textAnchor="end" className="tune-axis">0</text>
        <text x={pad - 4} y={y(hi - (hi - lo) * 0.08) + 3} textAnchor="end" className="tune-axis">{fmtR(Math.round(hi))}</text>
        {pts.map(([n, v, q]) => (
          <circle key={n} cx={x(n)} cy={y(v)} r={2.4} className={q ? (v >= 0 ? 'tune-dot up' : 'tune-dot down') : 'tune-dot unq'} />
        ))}
        {line.length ? <path d={line.join(' ')} className="tune-run" /> : null}
        {best?.qualified ? <circle cx={x(best.n)} cy={y(best.metrics[job.objective])} r={5} className="tune-dot best" /> : null}
        <text x={pad} y={H - 2} className="tune-axis">#1</text>
        <text x={W - 6} y={H - 2} textAnchor="end" className="tune-axis">#{job.budget}</text>
      </svg>
    </div>
  )
}
