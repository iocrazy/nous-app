/**
 * "Verify nous-center protocol" panel (Phase 2 closer).
 *
 * One-button admin affordance: click → calls
 * GET /api/v1/canvases/providers/nous-center/verify → shows the result.
 * Mounted at the bottom of the AI settings tab.
 *
 * The endpoint always returns 200 (failures in-band) so this component
 * doesn't need its own error-toast plumbing.
 */

import { useCallback, useState } from 'react';

import { apiFetch } from '../../../services/apiClient';

interface VerifyResult {
  ok: boolean;
  base_url?: string;
  workflows_visible?: number;
  status_code?: number;
  error?: string;
}

interface EnvelopeShape {
  success: boolean;
  data: VerifyResult;
}

type ProbeState =
  | { kind: 'idle' }
  | { kind: 'probing' }
  | { kind: 'done'; result: VerifyResult };

export function NousCenterVerifyPanel() {
  const [state, setState] = useState<ProbeState>({ kind: 'idle' });

  const run = useCallback(async () => {
    setState({ kind: 'probing' });
    try {
      const response = await apiFetch(
        '/api/v1/canvases/providers/nous-center/verify',
      );
      const body = (await response.json()) as EnvelopeShape;
      const result = body?.data ?? {
        ok: false,
        error: 'malformed response',
      };
      setState({ kind: 'done', result });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setState({
        kind: 'done',
        result: { ok: false, error: message },
      });
    }
  }, []);

  return (
    <section
      className="mt-6 rounded-md border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
      data-testid="nous-center-verify-panel"
    >
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            nous-center protocol
          </h3>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Pings the configured nous-center service to confirm the
            workflow-provider contract is live. Uses the server-side
            service token, not your account.
          </p>
        </div>
        <button
          type="button"
          onClick={run}
          disabled={state.kind === 'probing'}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300"
        >
          {state.kind === 'probing' ? 'Verifying…' : 'Verify protocol'}
        </button>
      </header>

      {state.kind === 'done' && <ProbeResult result={state.result} />}
    </section>
  );
}

function ProbeResult({ result }: { result: VerifyResult }) {
  if (result.ok) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
      >
        <p className="font-medium">✓ Service reachable</p>
        <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-xs">
          <dt>Base URL</dt>
          <dd className="truncate">{result.base_url}</dd>
          <dt>Workflows visible</dt>
          <dd>{result.workflows_visible}</dd>
        </dl>
      </div>
    );
  }
  return (
    <div
      role="status"
      aria-live="polite"
      className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:bg-rose-950 dark:text-rose-200"
    >
      <p className="font-medium">
        ✗ {result.status_code ? `HTTP ${result.status_code} — ` : ''}
        verification failed
      </p>
      <p className="mt-1 text-xs">{result.error ?? 'unknown reason'}</p>
    </div>
  );
}
