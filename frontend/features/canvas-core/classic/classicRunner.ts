/**
 * ClassicMode run-service seam (Phase 5a B5).
 *
 * The cascade orchestrator dispatches each runnable classic node through a
 * `ClassicRunner` — the SAME synchronous run path SmartMode's `runPrompts`
 * uses, just reshaped for classic node data. Tests inject a deterministic
 * runner; runtime uses `createClassicBackendRunner`, which reuses the
 * existing canvas run transport (POST /api/v1/canvases/runs/prompts) so we
 * do NOT invent a new endpoint.
 *
 * The abort signal threads through here: the cascade calls
 * `beginAbortable(nodeId)` and hands the runner `.signal`, which the
 * backend runner forwards into the fetch — so the comfy node view's Cancel
 * (which calls `abortNode(nodeId)`) aborts the in-flight cascade request.
 */

import { apiFetch, ApiError } from '../../../services/apiClient';

export interface ClassicRunContext {
  nodeId: string;
  /** Classic node type (llm / comfy / ...). Forwarded for forward-compat
   *  with a backend `run_classic_node` route; the current transport routes
   *  by the client-resolved `providerSlug`. */
  nodeType: string | undefined;
  /** The prompt/body text the node runs with. */
  body: string;
  /** Resolved by the dispatch table (`dispatchClassicNode`). null = the
   *  provider's default model. */
  providerSlug: string | null;
  /** AI-library agent id override, or null for the provider default. */
  agentId: string | null;
}

export interface ClassicRunResult {
  ok: boolean;
  /** Result text on success; empty on failure. */
  text: string;
  /** Populated on failure. */
  error: string | null;
}

/**
 * Runs ONE classic node to a terminal result. Always resolves — failures
 * are returned via `{ ok: false, error }`, not thrown — so the cascade can
 * sequence without try/catch boilerplate. `signal` aborts the in-flight
 * request.
 */
export type ClassicRunner = (
  ctx: ClassicRunContext,
  signal: AbortSignal,
) => Promise<ClassicRunResult>;

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
 * Build a ClassicRunner bound to a specific canvas. Reuses the same
 * in-band `/canvases/runs/prompts` route SmartMode uses — the route always
 * 200s normal runs and reports ok/failed in the body. We additionally send
 * `node_type` so a future backend upgrade can route through
 * `run_classic_node`; today the route consumes the client-resolved
 * `provider_slug`.
 */
export function createClassicBackendRunner({
  canvasId,
}: BackendRunnerOptions): ClassicRunner {
  return async (ctx, signal) => {
    try {
      const response = await apiFetch('/api/v1/canvases/runs/prompts', {
        method: 'POST',
        signal,
        json: {
          canvas_id: canvasId,
          prompt_node_id: ctx.nodeId,
          node_type: ctx.nodeType,
          body: ctx.body,
          provider_slug: ctx.providerSlug,
          agent_id: ctx.agentId,
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

function failed(error: string): ClassicRunResult {
  return { ok: false, text: '', error };
}
