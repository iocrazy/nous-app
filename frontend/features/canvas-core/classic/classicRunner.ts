/**
 * ClassicMode run-service seam (Phase 5a path B).
 *
 * The cascade orchestrator dispatches each runnable classic node through a
 * `ClassicRunner`. Tests inject a deterministic runner; runtime uses
 * `createClassicBackendRunner`, which POSTs the WHOLE node to the new
 * server-resolving route `POST /api/v1/canvases/runs/classic-node`. The
 * backend (`resolve_classic_dispatch`) decides how every runnable type runs
 * — image_gen → generate_image, video_gen → generate_video, comfy →
 * nous/<slug>, llm → text adapter. The frontend no longer guesses a
 * provider_slug (the old mirror is dead).
 *
 * The abort signal threads through here: the cascade calls
 * `beginAbortable(nodeId)` and hands the runner `.signal`, which the
 * backend runner forwards into the fetch — so a node view's Cancel (which
 * calls `abortNode(nodeId)`) aborts the in-flight cascade request.
 */

import { apiFetch, ApiError } from '../../../services/apiClient';

export interface ClassicRunContext {
  nodeId: string;
  /** Classic node type (llm / comfy / image_gen / video_gen / ...). Sent to
   *  the backend, which resolves the dispatch route from it + `data`. */
  nodeType: string | undefined;
  /** The node's opaque `data` blob — POSTed verbatim as `node.data`; the
   *  backend reads provider/workflow/op params (e.g. data.prompt,
   *  data.workflow_slug) out of it. */
  data: Record<string, unknown>;
  /** Upstream/aggregated text fed into the node (e.g. the prompt for an llm
   *  node). image_gen prefers its own data.prompt but falls back to this. */
  body: string;
  /** AI-library agent id override, or null for the provider default. */
  agentId: string | null;
}

export interface ClassicRunResult {
  ok: boolean;
  /** Result text on success; empty on failure. */
  text: string;
  /** Populated on failure. */
  error: string | null;
  /** Structured op output on success — e.g. `{ image_url }` for image_gen or
   *  `{ video_url, thumbnail_url, ... }` for video_gen. null for plain text /
   *  on failure. */
  result?: Record<string, unknown> | null;
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
    result?: Record<string, unknown> | null;
    response_kind?: string;
  };
}

/**
 * Build a ClassicRunner bound to a specific canvas. POSTs the node to the
 * server-resolving `/canvases/runs/classic-node` route: the route always
 * 200s normal runs and reports ok/failed in the body. `canvas_id` is bound
 * at construction (the cascade reads it from the canvas-core store when it
 * builds the runner) so the runner never has to reach into the store itself.
 */
export function createClassicBackendRunner({
  canvasId,
}: BackendRunnerOptions): ClassicRunner {
  return async (ctx, signal) => {
    try {
      const response = await apiFetch('/api/v1/canvases/runs/classic-node', {
        method: 'POST',
        signal,
        json: {
          canvas_id: canvasId,
          node: {
            id: ctx.nodeId,
            type: ctx.nodeType,
            data: ctx.data,
          },
          body: ctx.body,
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
        result: data.ok ? (data.result ?? null) : null,
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
  return { ok: false, text: '', error, result: null };
}
