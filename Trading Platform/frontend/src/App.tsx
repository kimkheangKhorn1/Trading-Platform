import { useEffect, useRef } from 'react'
import { ChartView } from './chart/ChartView'
import { ChartFooter } from './components/ChartFooter'
import { ErrorBoundary } from './components/common/ErrorBoundary'
import { Icon } from './components/common/Icon'
import { DataDialog, GotoDialog, SymbolInfoDialog } from './components/DataDialog'
import { IndicatorsDialog } from './components/IndicatorsDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { TuningDialog } from './components/TuningDialog'
import { StrategyTester } from './components/tester/StrategyTester'
import { TopBar } from './components/TopBar'
import { useApp } from './state/app'
import { useIndicators } from './state/indicators'
import { useTester } from './state/tester'

function Dialogs() {
  const dialog = useApp((s) => s.dialog)
  if (!dialog) return null
  switch (dialog.type) {
    case 'indicators':
      return <IndicatorsDialog />
    case 'settings':
      return <SettingsDialog uid={dialog.uid} />
    case 'tuning':
      return <TuningDialog uid={dialog.uid} />
    case 'data':
      return <DataDialog />
    case 'symbol':
      return <SymbolInfoDialog />
    case 'goto':
      return <GotoDialog />
  }
}

/** Drag handle between chart and tester (TradingView-style). */
function Resizer() {
  const setTester = useTester((s) => s.set)
  const open = useTester((s) => s.open)
  const drag = useRef<{ y: number; h: number } | null>(null)
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!drag.current) return
      const h = Math.max(160, Math.min(window.innerHeight - 220, drag.current.h + drag.current.y - e.clientY))
      setTester({ height: h })
    }
    const up = () => {
      drag.current = null
      document.body.classList.remove('resizing')
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [setTester])
  if (!open) return null
  return (
    <div className="resizer" onMouseDown={(e) => {
      drag.current = { y: e.clientY, h: useTester.getState().height }
      document.body.classList.add('resizing')
    }} />
  )
}

export default function App() {
  const init = useApp((s) => s.init)
  const backendError = useApp((s) => s.backendError)
  const scripts = useApp((s) => s.scripts)
  const feeds = useApp((s) => s.feeds)
  const openDialog = useApp((s) => s.openDialog)
  const health = useApp((s) => s.health)

  useEffect(() => {
    void init().then(() => useTester.getState().restoreLatest())
  }, [init])

  useEffect(() => {
    // drops saved chart instances of scripts that no longer exist
    if (scripts.length) useIndicators.getState().syncWithMeta(scripts)
  }, [scripts])

  if (backendError) {
    return (
      <div className="boot-error">
        <Icon name="warning" size={28} />
        <h2>Cannot reach the backend</h2>
        <p>{backendError}</p>
        <p className="muted">Start it with <code>./start.sh</code> (or <code>cd backend && .venv/bin/uvicorn app.main:app --port 8010</code>), then retry.</p>
        <button className="btn primary" onClick={() => void init()}>Retry</button>
      </div>
    )
  }

  return (
    <div className="app">
      <TopBar />
      <main className="workspace">
        <section className="chart-area">
          {health && feeds.length === 0 ? (
            <div className="first-run">
              <h2>No market data yet</h2>
              <p>
                {health.market_data_provider === 'exness'
                  ? (health.exness?.last_error ?? 'Exness API access needs setup. Open Data for connection status, or import a CSV while credentials are being prepared.')
                  : health.oanda.enabled
                  ? 'OANDA is configured but no feed could be initialised - check the API token and network, then reload.'
                  : 'OANDA credentials are not configured. Import XAUUSD candles from a CSV file to get started, or add OANDA_API_TOKEN to backend/.env and restart the backend.'}
              </p>
              <button className="btn primary" onClick={() => openDialog({ type: 'data' })}>
                <Icon name="upload" size={15} /> Open data manager
              </button>
            </div>
          ) : (
            <ErrorBoundary name="Chart">
              <ChartView />
            </ErrorBoundary>
          )}
          <ChartFooter />
        </section>
        <Resizer />
        <ErrorBoundary name="Strategy Tester">
          <StrategyTester />
        </ErrorBoundary>
      </main>
      <ErrorBoundary name="Dialog">
        <Dialogs />
      </ErrorBoundary>
    </div>
  )
}
