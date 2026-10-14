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
      <div className="m-3 rounded-lg border border-red-400/30 bg-red-500/5 p-3 text-xs text-red-200">
        <p className="font-medium">The {this.props.name} panel hit an error.</p>
        <p className="mt-1 text-red-300/80">{this.state.error.message}</p>
        <button onClick={() => this.setState({ error: null })} className="mt-2 rounded bg-red-400/15 px-2 py-1 hover:bg-red-400/25">
          Try again
        </button>
      </div>
    )
  }
}
