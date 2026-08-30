import { Component, type ErrorInfo, type ReactNode } from 'react'

import AppBackground from './AppBackground'

interface State {
  failed: boolean
}

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('GraphDrift render failure', error, info)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <main className="relative flex min-h-screen items-center justify-center p-6">
        <AppBackground />
        <section className="glass-panel-elevated relative z-10 max-w-lg rounded-2xl p-8 text-center">
          <h1 className="font-headline text-2xl text-on-surface">Something went wrong</h1>
          <p className="mt-3 text-sm text-on-surface-variant">
            The interface could not render safely. Reload to restore a clean session.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-6 rounded-lg bg-primary/20 px-5 py-2 text-sm text-primary"
          >
            Reload application
          </button>
        </section>
      </main>
    )
  }
}
