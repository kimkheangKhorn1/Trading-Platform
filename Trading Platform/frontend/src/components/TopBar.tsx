import { useEffect, useRef, useState } from 'react'
import { ALL_TFS, currentFeed, TF_LABELS, useApp } from '../state/app'
import { useReplay } from '../state/replay'
import { useRuler } from '../state/ruler'
import { Icon } from './common/Icon'

const LIVE_LABEL: Record<string, string> = {
  live: 'Live', connecting: 'Connecting', reconnecting: 'Reconnecting', offline: 'Offline', static: 'Historical data',
  error: 'Error', disconnected: 'Backend offline',
}

export function TopBar() {
  const feeds = useApp((s) => s.feeds)
  const feedId = useApp((s) => s.feedId)
  const tf = useApp((s) => s.timeframe)
  const setFeed = useApp((s) => s.setFeed)
  const setTimeframe = useApp((s) => s.setTimeframe)
  const openDialog = useApp((s) => s.openDialog)
  const liveState = useApp((s) => s.liveState)
  const liveMessage = useApp((s) => s.liveMessage)
  const replayMode = useReplay((s) => s.mode)
  const setReplay = useReplay((s) => s.set)
  const rulerOn = useRuler((s) => s.enabled)
  const [menu, setMenu] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const feed = currentFeed()
  useApp((s) => s.feeds) // re-render when feeds load

  useEffect(() => {
    if (!menu) return
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenu(false)
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [menu])

  return (
    <header className="topbar">
      <div className="brand" title="Local XAUUSD trading platform">
        <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden><path d="M8 7v18M8 11h-2v8h2M16 5v20M16 9h-2v10h2M24 9v16M24 13h2v6h-2" stroke="#d4a017" strokeWidth="2.2" fill="none" /></svg>
      </div>
      <div className="symbol-picker" ref={menuRef}>
        <button className="symbol-btn" onClick={() => setMenu(!menu)} aria-haspopup="listbox" aria-expanded={menu}>
          <Icon name="search" size={16} />
          <span className="sym">{feed?.symbol ?? 'No data'}</span>
          {feed ? <span className={`src-badge ${feed.provider}`}>{feed.provider === 'exness_archive' ? 'EXNESS HISTORY' : feed.provider.toUpperCase()}</span> : null}
          <Icon name="chevronDown" size={14} />
        </button>
        {menu ? (
          <div className="menu" role="listbox">
            <div className="menu-head">Data feeds</div>
            {feeds.length === 0 ? <div className="menu-empty">No data yet — download Exness archive history or import a CSV.</div> : null}
            {feeds.map((f) => (
              <button key={f.id} role="option" aria-selected={f.id === feedId} className={`menu-item ${f.id === feedId ? 'sel' : ''}`}
                onClick={() => { setFeed(f.id); setMenu(false) }}>
                <span className="sym">{f.symbol}</span>
                <span className="muted">{f.display_name}</span>
                {f.live ? <span className="dot-live" title="Live updates" /> : null}
              </button>
            ))}
            <div className="menu-sep" />
            <button className="menu-item" onClick={() => { setMenu(false); openDialog({ type: 'symbol' }) }}>
              <Icon name="info" size={15} /> Symbol info
            </button>
            <button className="menu-item" onClick={() => { setMenu(false); openDialog({ type: 'data' }) }}>
              <Icon name="database" size={15} /> Data manager…
            </button>
          </div>
        ) : null}
      </div>
      <div className="sep" />
      <div className="tf-group" role="group" aria-label="Timeframe">
        {ALL_TFS.map((code) => {
          const available = !feed || feed.timeframes.includes(code)
          return (
            <button key={code} className={`tf-btn ${code === tf ? 'active' : ''}`} disabled={!available}
              title={available ? undefined : 'Not available for this data feed'} onClick={() => setTimeframe(code)}>
              {TF_LABELS[code]}
            </button>
          )
        })}
      </div>
      <div className="sep" />
      <button className="tb-btn" onClick={() => openDialog({ type: 'indicators' })}>
        <span className="fx">ƒx</span> Indicators
      </button>
      <button className={`tb-btn ${replayMode !== 'off' ? 'on' : ''}`} title="Bar replay"
        onClick={() => {
          useRuler.getState().setEnabled(false)
          setReplay(replayMode === 'off' ? { mode: 'picking', playing: false }
            : { mode: 'off', playing: false, cursorTime: null, cutoffTime: null, startTime: null })
        }}
        disabled={replayMode === 'active'}>
        <Icon name="replay" size={17} /> Replay
      </button>
      <button className={`tb-btn ${rulerOn ? 'on' : ''}`} aria-label="Ruler" aria-pressed={rulerOn}
        title="Measure price and time (Esc closes)" onClick={() => {
          if (replayMode === 'picking') setReplay({ mode: useReplay.getState().cutoffTime === null ? 'off' : 'active' })
          useRuler.getState().setEnabled(!rulerOn)
        }}>
        <Icon name="ruler" size={17} /> Ruler
      </button>
      <button className="tb-btn" title="Go to date" onClick={() => openDialog({ type: 'goto' })}>
        <Icon name="calendar" size={17} />
      </button>
      <div className="grow" />
      <button className="tb-btn" onClick={() => openDialog({ type: 'data' })}>
        <Icon name="database" size={17} /> Data
      </button>
      {feed ? (
        <div className={`live-pill ${liveState}`} title={liveMessage || LIVE_LABEL[liveState]}>
          <span className="dot" />
          {LIVE_LABEL[liveState] ?? liveState}
        </div>
      ) : null}
    </header>
  )
}
