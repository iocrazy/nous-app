import { apiClient } from '../client'

const SESSION_ID = crypto.randomUUID()

interface ErrorReport {
  error_type: string
  message: string
  stack?: string
  url?: string
  component?: string
  metadata?: Record<string, unknown>
}

export function reportError(report: ErrorReport): void {
  // Fire-and-forget — never await, never throw
  apiClient
    .post('/api/v1/errors/report', {
      ...report,
      session_id: SESSION_ID,
      user_agent: navigator.userAgent,
    })
    .catch(() => {
      // Intentionally swallowed — error reporting must not cause errors
    })
}
