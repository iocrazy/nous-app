import React, { Component, ErrorInfo, ReactNode } from 'react';
import { reportError } from '../services/errorReporter';

interface Props {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Top-level React ErrorBoundary.
 *
 * Catches render-time errors that escape component-local handling,
 * forwards them to the error reporter so they land in
 * frontend_error_logs, and renders a minimal recovery UI.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    void reportError(error, {
      type: 'react_boundary',
      component: info.componentStack?.trim().split('\n')[0]?.trim(),
      metadata: { componentStack: info.componentStack?.slice(0, 4000) },
    });
  }

  private reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset);
    }

    return (
      <div className="min-h-screen bg-black text-zinc-200 flex items-center justify-center p-8">
        <div className="max-w-lg w-full">
          <h1 className="text-xl font-semibold mb-2">Something went wrong</h1>
          <p className="text-sm text-zinc-400 mb-4">
            An unexpected error occurred. The error has been reported.
          </p>
          <pre className="text-xs bg-zinc-900 text-red-300 p-3 rounded overflow-auto max-h-48 mb-4">
            {error.message}
          </pre>
          <button
            type="button"
            onClick={this.reset}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
