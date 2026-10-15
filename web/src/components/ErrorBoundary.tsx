import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/** Contains a crash to one panel instead of blanking the whole app. */
export class ErrorBoundary extends Component<{ name: string; children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.name}]`, error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="m-6 rounded-xl bg-danger-soft p-4 text-sm text-danger">
        <p className="font-medium">Something went wrong on this page.</p>
        <p className="mt-1 text-muted">{this.state.error.message}</p>
        <button onClick={() => this.setState({ error: null })} className="mt-3 rounded-lg border border-line bg-surface px-3 py-1.5 text-fg hover:bg-surface-2">
          Try again
        </button>
      </div>
    )
  }
}
