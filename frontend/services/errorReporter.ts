/**
 * Frontend error reporter — wires unhandled errors into the
 * /errors/report endpoint (which writes to frontend_error_logs).
 *
 * The table existed but nothing wrote to it, so the audit trail was blind.
 * This module closes that gap.
 *
 * Design goals:
 * - Must never throw or block the app (error reporting failures are swallowed)
 * - Dedup identical errors within a short window to avoid flooding the backend
 * - Attach stable session_id so multi-error sessions can be grouped later
 * - Send via a plain `fetch` (not apiClient) to avoid infinite loops if the
 *   reporter itself fails
 */

import { getApiUrl } from '../utils/apiConfig';
import { getSupabaseAccessToken } from '../supabaseClient';

type ErrorType = 'runtime' | 'network' | 'unhandled_rejection' | 'react_boundary';

interface ErrorReport {
  error_type: ErrorType;
  message: string;
  stack?: string;
  url?: string;
  component?: string;
  session_id?: string;
  user_agent?: string;
  metadata?: Record<string, unknown>;
}

// Stable per-tab session id so grouped errors in monitoring line up.
function ensureSessionId(): string {
  const key = 'mediahub_error_session_id';
  try {
    const existing = window.sessionStorage?.getItem(key);
    if (existing) return existing;
    const fresh =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2);
    window.sessionStorage?.setItem(key, fresh);
    return fresh;
  } catch {
    return 'anon';
  }
}

// Dedup identical (message + stack[:100]) reports within a short window.
const DEDUP_WINDOW_MS = 10_000;
const recentFingerprints = new Map<string, number>();

function shouldSkip(fingerprint: string): boolean {
  const now = Date.now();
  // Evict stale entries; keep the map small.
  for (const [key, ts] of recentFingerprints) {
    if (now - ts > DEDUP_WINDOW_MS) recentFingerprints.delete(key);
  }
  const last = recentFingerprints.get(fingerprint);
  if (last && now - last < DEDUP_WINDOW_MS) return true;
  recentFingerprints.set(fingerprint, now);
  return false;
}

async function postReport(report: ErrorReport): Promise<void> {
  try {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    try {
      const token = await getSupabaseAccessToken();
      if (token) headers['Authorization'] = `Bearer ${token}`;
    } catch {
      // No auth available — endpoint accepts anonymous reports.
    }

    await fetch(`${getApiUrl()}/api/v1/errors/report`, {
      method: 'POST',
      headers,
      body: JSON.stringify(report),
      keepalive: true,
    });
  } catch {
    // Reporter failures must never surface — they'd amplify the original bug.
  }
}

/**
 * Send a report for a captured error.
 * Safe to call from anywhere; failures are swallowed and dedup'd.
 */
export async function reportError(
  error: unknown,
  opts: {
    type?: ErrorType;
    component?: string;
    metadata?: Record<string, unknown>;
  } = {},
): Promise<void> {
  const err = normalizeError(error);
  const fingerprint = `${err.message}::${(err.stack ?? '').slice(0, 100)}`;
  if (shouldSkip(fingerprint)) return;

  const report: ErrorReport = {
    error_type: opts.type ?? 'runtime',
    message: err.message.slice(0, 2000),
    stack: err.stack?.slice(0, 8000),
    url: typeof window !== 'undefined' ? window.location.href : undefined,
    component: opts.component,
    session_id: ensureSessionId(),
    user_agent:
      typeof navigator !== 'undefined' ? navigator.userAgent : undefined,
    metadata: opts.metadata,
  };

  void postReport(report);
}

function normalizeError(error: unknown): { message: string; stack?: string } {
  if (error instanceof Error) {
    return { message: error.message || String(error), stack: error.stack };
  }
  if (typeof error === 'string') {
    return { message: error };
  }
  try {
    return { message: JSON.stringify(error) };
  } catch {
    return { message: String(error) };
  }
}

let installed = false;

/**
 * Install global handlers for window errors and unhandled promise rejections.
 * Idempotent — safe to call once at app startup.
 */
export function installErrorReporter(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  window.addEventListener('error', (event: ErrorEvent) => {
    void reportError(event.error ?? event.message, {
      type: 'runtime',
      metadata: {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
      },
    });
  });

  window.addEventListener(
    'unhandledrejection',
    (event: PromiseRejectionEvent) => {
      void reportError(event.reason, { type: 'unhandled_rejection' });
    },
  );
}

// Exposed for tests.
export const __test = { ensureSessionId, normalizeError, shouldSkip };
