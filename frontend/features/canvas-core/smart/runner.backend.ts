/**
 * Backend-backed prompt runner (Phase 2 Day 6-8).
 *
 * Drops into the same `PromptCaller` seam the composer already uses —
 * the swap from mock to backend is one prop on `CanvasComposer`. The
 * backend route lives at POST /api/v1/canvases/runs/prompts and
 * returns the result in-band (the route always 200s normal runs).
 *
 * Requires the caller's canvas_id, which the composer reads from the
 * store at call time. We close over it via a small factory below.
 */

import { apiFetch, ApiError } from '../../../services/apiClient';
import type { PromptCaller, RunnerResult } from './runner';

interface BackendRunnerOptions {
  canvasId: string;
}

interface BackendEnvelope {
  success: boolean;
  data?: {
    ok: boolean;
    text: string;
    error: string | null;
    response_kind?: string;
  };
}

/**
 * Build a PromptCaller bound to a specific canvas. The composer wraps
 * this in a useMemo keyed on canvasId so the runner reference is
 * stable across renders.
 */
export function createBackendRunner({
  canvasId,
}: BackendRunnerOptions): PromptCaller {
  return async (ctx) => {
    try {
      const response = await apiFetch('/api/v1/canvases/runs/prompts', {
        method: 'POST',
        json: {
          canvas_id: canvasId,
          prompt_node_id: ctx.promptId,
          body: ctx.body,
          provider_slug: ctx.provider_slug || null,
          agent_id: ctx.agent_id,
        },
      });
      const envelope = (await response.json()) as BackendEnvelope;
      if (!envelope.success || !envelope.data) {
        return failed('malformed backend response');
      }
      const data = envelope.data;
      return {
        ok: data.ok,
        text: data.ok ? data.text : '',
        error: data.ok ? null : (data.error ?? 'unknown backend error'),
      };
    } catch (err) {
      if (err instanceof ApiError) {
        return failed(`HTTP ${err.status}: ${err.message}`);
      }
      const message = err instanceof Error ? err.message : String(err);
      return failed(message);
    }
  };
}

function failed(error: string): RunnerResult {
  return { ok: false, text: '', error };
}
