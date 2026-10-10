import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Icon } from './Icon'

/** Keeps a failure in one panel (chart, tester, dialog) from blanking the whole application. */
export class ErrorBoundary extends Component<{ name: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.name}]`, error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="panel-error">
        <Icon name="warning" size={18} />
        <div>
          <b>{this.props.name} failed to render.</b>
          <div className="muted small mono">{this.state.error.message}</div>
        </div>
        <button className="btn ghost sm" onClick={() => this.setState({ error: null })}>Retry</button>
      </div>
    )
  }
}
