import { useState } from 'react'
import { Icon, Spinner } from '../components/common/Icon'
import { fmtPrice } from '../lib/format'
import { currentFeed, TF_LABELS, useApp } from '../state/app'
import { useIndicators } from '../state/indicators'
import type { ChartController } from './controller'
import { BadgeOverlapControl } from './BadgeOverlap'

function fmtVolume(v: number): string {
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (v >= 1e4) return `${(v / 1e3).toFixed(1)}K`
  if (v >= 100) return Math.round(v).toLocaleString('en-US')
  return v.toFixed(v >= 10 ? 1 : 2)
}

export function Legend({ ctrl, hoverIdx }: { ctrl: ChartController; hoverIdx: number | null; version: number }) {
  const tf = useApp((s) => s.timeframe)
  const scripts = useApp((s) => s.scripts)
  const openDialog = useApp((s) => s.openDialog)
  useApp((s) => s.feeds)
  const instances = useIndicators((s) => s.instances)
  const outputs = useIndicators((s) => s.outputs)
  const update = useIndicators((s) => s.update)
  const remove = useIndicators((s) => s.remove)
  const [collapsed, setCollapsed] = useState(false)
  const feed = currentFeed()
  const archiveSnapshot = feed?.provider === 'exness_archive' && ctrl.replayIndex === null
  const bars = ctrl.shownBars
  const n = bars.length
  const idx = hoverIdx !== null && hoverIdx >= 0 && hoverIdx < n ? hoverIdx : n - 1
  const bar = idx >= 0 ? bars[idx] : null
  const prev = idx > 0 ? bars[idx - 1] : null
  const p = ctrl.precision
  const change = bar && prev ? bar[4] - prev[4] : null
  const up = bar ? bar[4] >= bar[1] : true
  const cls = up ? 'up' : 'down'

  return (
    <div className="legend">
      <div className="legend-row main">
        <span className="legend-symbol">{feed?.symbol ?? '—'}</span>
        <span className="legend-dot">·</span>
        <span>{TF_LABELS[tf]}</span>
        <span className="legend-dot">·</span>
        <span className="muted">{feed ? (feed.provider === 'csv' ? `CSV ${feed.id.split('-').slice(2).join('-')}`
          : feed.provider === 'exness_archive' ? 'EXNESS HISTORY' : feed.provider.toUpperCase()) : ''}</span>
        {bar ? (
          <span className="legend-ohlc">
            <span>O<b className={cls}>{fmtPrice(bar[1], p)}</b></span>
            <span>H<b className={cls}>{fmtPrice(bar[2], p)}</b></span>
            <span>L<b className={cls}>{fmtPrice(bar[3], p)}</b></span>
            <span>C<b className={cls}>{fmtPrice(bar[4], p)}</b></span>
            {change !== null && prev ? (
              <b className={change >= 0 ? 'up' : 'down'}>
                {change >= 0 ? '+' : ''}{fmtPrice(change, p)} ({change >= 0 ? '+' : ''}{((change / prev[4]) * 100).toFixed(2)}%)
              </b>
            ) : null}
            <span>Vol<b className="muted-strong">{fmtVolume(bar[5])}</b></span>
            {bar[6] === 0 ? <span className="pill-live" title={archiveSnapshot
              ? 'The archive ends partway through this candle. It updates when new archive data is imported.'
              : 'This candle has not closed yet.'}>{archiveSnapshot ? 'partial snapshot' : 'forming'}</span> : null}
          </span>
        ) : null}
      </div>
      {instances.length ? (
        <div className="legend-indicators">
          <button className="legend-collapse" onClick={() => setCollapsed(!collapsed)}
            title={collapsed ? 'Show indicators' : 'Hide indicator list'}
            aria-label={collapsed ? 'Show indicators' : 'Hide indicator list'} aria-expanded={!collapsed}>
            <Icon name={collapsed ? 'chevronDown' : 'chevronUp'} size={12} />
          </button>
          <div className="legend-indicator-rows">
            {!collapsed &&
              instances.map((inst) => {
                const meta = scripts.find((s) => s.id === inst.scriptId)
                if (!meta) return null
                const out = outputs[inst.uid]
                const data = out?.data
                const args = meta.inputs.filter((i) => i.type === 'int' || i.type === 'source').slice(0, 3).map((i) => String(inst.inputs[i.id]))
                const hiddenTf = !inst.timeframes.includes(tf)
                return (
                  <div key={inst.uid} className={`legend-row ind ${inst.visible && !hiddenTf ? '' : 'dim'}`}>
                    <span className="legend-name" onDoubleClick={() => openDialog({ type: 'settings', uid: inst.uid })}>
                      {meta.short_name} <span className="muted">{args.join(' ')}</span>
                    </span>
                    <span className="legend-actions">
                      {inst.scriptId === 'smc_ict_scalping_master' ? <BadgeOverlapControl inst={inst} update={update} /> : null}
                      <button className="icon-btn sm" title={inst.visible ? 'Hide' : 'Show'} onClick={() => update(inst.uid, { visible: !inst.visible })}>
                        <Icon name={inst.visible ? 'eye' : 'eyeOff'} size={14} />
                      </button>
                      <button className="icon-btn sm" title="Settings" aria-label={`Settings for ${meta.short_name}`} onClick={() => openDialog({ type: 'settings', uid: inst.uid })}>
                        <Icon name="gear" size={13} />
                      </button>
                      <button className="icon-btn sm" title="Remove" onClick={() => remove(inst.uid)}>
                        <Icon name="close" size={13} />
                      </button>
                    </span>
                    {hiddenTf ? <span className="muted">hidden on {TF_LABELS[tf]}</span> : null}
                    {out?.loading ? <Spinner size={11} /> : null}
                    {inst.visible && !hiddenTf && data?.warnings?.length ? (
                      <span className="legend-warn" title={data.warnings.join('\n')}>
                        <Icon name="warning" size={13} /> {data.warnings[0]}{data.warnings.length > 1 ? ` (+${data.warnings.length - 1})` : ''}
                      </span>
                    ) : null}
                    {out?.error ? (
                      <span className="legend-err" title={out.error}>
                        <Icon name="warning" size={13} /> {out.error.length > 60 ? `${out.error.slice(0, 60)}…` : out.error}
                      </span>
                    ) : null}
                  </div>
                )
              })}
          </div>
        </div>
      ) : null}
    </div>
  )
}
