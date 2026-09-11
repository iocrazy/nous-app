/**
 * Canvas generation REST client (Infinite-Canvas parity Phase 2 G4-B1/F1).
 *
 * Matches `backend/app/api/canvases_router.py`'s generation endpoints:
 * model catalog for the composer picker, count-fan-out dispatch (each item
 * is an independent DBOS task — the queue governs concurrency, the browser
 * never opens its own parallelism), and task polling until a terminal
 * phase. The durable result lands in `metadata.result_url` (a same-origin
 * /cover|/stream URL a bare <img>/<video> can load).
 */

import { apiFetch, ApiError } from '../../../services/apiClient';

export interface GenerationModel {
  name: string;
  display_name: string;
  type: 'image' | 'video';
  /** True when this row runs on the viewer's own machine via the paired
   *  codex daemon. (The raw provider name is deliberately not exposed —
   *  2026-08-14 leak tripwire.) */
  is_local?: boolean;
  sort_order?: number;
}

export interface GenerationDispatchRequest {
  node_id: string;
  kind: 'image' | 'video';
  prompt: string;
  model?: string;
  count?: number;
  params?: Record<string, unknown>;
  source_url?: string | null;
}

export interface GenerationTask {
  phase: string;
  status?: string;
  error_msg?: string | null;
  metadata?: {
    result_url?: string;
    generated_media_id?: number;
    media_kind?: string;
    /** Knobs the request asked for that this provider cannot honour (P2).
     *  Always written by the workflow, `[]` meaning "nothing dropped" — an
     *  ABSENT key means an older task row, not a clean run. */
    dropped_knobs?: string[];
    /** References the run could not resolve, each with a reason code (P4
     *  asset library). Same contract as `dropped_knobs`: always written,
     *  `[]` meaning "every reference was used", an ABSENT key meaning an
     *  older task row. Reported SEPARATELY from `dropped_knobs` because a
     *  run can drop a knob, a reference, or both. */
    dropped_refs?: Array<{ url: string; reason: string }>;
    /** Written by the workflow when a failure brought an explanation for
     *  the user (2026-09-05): `detail` is the model's own words for a
     *  content refusal — why, and the rewrite it offers. Chinese-safe here
     *  (jsonb) where `error_msg` is not. Empty `detail` = the provider
     *  could not say (e.g. a 0.4.0 daemon). */
    failure?: { code?: string; detail?: string };
    [k: string]: unknown;
  };
}

const TERMINAL_PHASES = new Set(['completed', 'failed', 'cancelled', 'lost']);
/** Infinite polls image tasks every 2s for up to 30min — same envelope. */
const DEFAULT_POLL_INTERVAL_MS = 2000;
const DEFAULT_POLL_TIMEOUT_MS = 30 * 60 * 1000;

export async function listGenerationModels(): Promise<GenerationModel[]> {
  const response = await apiFetch('/api/v1/canvases/generation-models');
  const body = (await response.json()) as { success: boolean; data?: GenerationModel[] };
  if (!body.success || !Array.isArray(body.data)) {
    throw new ApiError('generation-models response missing data', 500);
  }
  return body.data;
}

/** What one catalog model can actually honour, as projected by the backend
 *  (`GET /api/v1/canvases/generation-capabilities`). The UI hides a knob only
 *  when a model is present here and says no — absence means "unknown", never
 *  "unsupported". `honours_ratio` is deliberately absent: it names an internal
 *  strategy, not something the UI can act on. */
export interface ModelCapabilities {
  ratios: string[];
  quality: boolean;
  resolution: boolean;
  max_refs: number;
  negative: boolean;
  video_modes: string[];
}

/** Per-model capabilities keyed by catalog model name. Visibility matches
 *  `listGenerationModels` row for row (both endpoints read one server-side
 *  predicate), so every model the picker offers has an entry here. */
export async function listGenerationCapabilities(): Promise<Record<string, ModelCapabilities>> {
  const response = await apiFetch('/api/v1/canvases/generation-capabilities');
  const body = (await response.json()) as {
    success: boolean;
    data?: Record<string, ModelCapabilities>;
  };
  if (!body.success || !body.data || typeof body.data !== 'object' || Array.isArray(body.data)) {
    throw new ApiError('generation-capabilities response missing data', 500);
  }
  return body.data;
}

/** An enabled `llm` catalog row for the text prompt's model picker. Same
 *  public-field contract as GenerationModel — the two come from the same
 *  `mediahub_models` table, differing only in `type`. */
export interface TextModel {
  name: string;
  display_name: string;
  type: 'llm';
  actual_provider: string;
  sort_order?: number;
}

/** Enabled llm models from the platform catalog (public columns only) — the
 *  single source of truth for the text prompt node's model dropdown, replacing
 *  the old hardcoded PROVIDER_OPTIONS that named models the platform doesn't
 *  carry (the 2026-07-12 "default prompt won't run" root cause). */
export async function listTextModels(): Promise<TextModel[]> {
  const response = await apiFetch('/api/v1/canvases/text-models');
  const body = (await response.json()) as { success: boolean; data?: TextModel[] };
  if (!body.success || !Array.isArray(body.data)) {
    throw new ApiError('text-models response missing data', 500);
  }
  return body.data;
}

export async function dispatchGenerations(
  canvasId: string,
  req: GenerationDispatchRequest,
): Promise<string[]> {
  const response = await apiFetch(`/api/v1/canvases/${canvasId}/generations`, {
    method: 'POST',
    json: {
      node_id: req.node_id,
      kind: req.kind,
      prompt: req.prompt,
      model: req.model ?? '',
      count: req.count ?? 1,
      params: req.params ?? {},
      ...(req.source_url ? { source_url: req.source_url } : {}),
    },
  });
  const body = (await response.json()) as { success: boolean; task_ids?: string[] };
  if (!body.success || !Array.isArray(body.task_ids)) {
    throw new ApiError('generation dispatch response missing task_ids', 500);
  }
  return body.task_ids;
}

export async function getGeneration(taskId: string): Promise<GenerationTask> {
  const response = await apiFetch(`/api/v1/canvases/generations/${taskId}`);
  const body = (await response.json()) as { success: boolean; data?: GenerationTask };
  if (!body.success || !body.data) {
    throw new ApiError('generation task response missing data', 500);
  }
  return body.data;
}

/** Really cancel one generation task server-side (P1-1). DELETE asks the DBOS
 *  engine to cancel; the mirror trigger flips task_tracking to phase=cancelled.
 *  Idempotent on the backend (cancelling a terminal task is a no-op), so the
 *  caller can fire-and-forget on Stop without racing task completion. */
export async function cancelGeneration(taskId: string): Promise<void> {
  await apiFetch(`/api/v1/canvases/generations/${taskId}`, { method: 'DELETE' });
}

/** Thrown by pollGeneration when the caller's shouldStop trips — the frontend
 *  wait is abandoned. Stop also fires cancelGeneration() for the in-flight
 *  tasks (P1-1), so the backend workflow really stops instead of running on. */
export class PollStopped extends Error {
  constructor(taskId: string) {
    super(`polling for generation task ${taskId} stopped by user`);
    this.name = 'PollStopped';
  }
}

/** Poll one task until it reaches a terminal phase (default 2s / 30min). */
export async function pollGeneration(
  taskId: string,
  opts: {
    intervalMs?: number;
    timeoutMs?: number;
    /** Fired on every non-terminal poll — carries the live task row. */
    onTick?: (task: GenerationTask) => void;
    /** Cooperative stop (P0-4) — checked once per poll cycle. */
    shouldStop?: () => boolean;
  } = {},
): Promise<GenerationTask> {
  const intervalMs = opts.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_POLL_TIMEOUT_MS;
  const startedAt = Date.now();
  for (;;) {
    if (opts.shouldStop?.()) throw new PollStopped(taskId);
    const task = await getGeneration(taskId);
    if (TERMINAL_PHASES.has(task.phase)) return task;
    opts.onTick?.(task);
    if (Date.now() - startedAt >= timeoutMs) {
      throw new Error(`generation task ${taskId} timed out after ${timeoutMs}ms`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

// ---- Timeline director (G8) ----------------------------------------------

export interface TimelineRunRequest {
  node_id: string;
  segments: Array<{ prompt: string; seconds: number }>;
  model: string;
  aspect: string;
}

// ---- Download-all zip (P2-7) ---------------------------------------------

export interface ZipAssetItem {
  url: string;
  name: string;
}

/** Bundle several generated-media results into one archive server-side
 *  (P2-7). Returns the zip blob; the auth header rides via apiFetch. Throws
 *  ApiError on a non-2xx so the caller can fall back to per-file download. */
export async function downloadCanvasAssetsZip(
  filename: string,
  items: ZipAssetItem[],
): Promise<Blob> {
  const response = await apiFetch('/api/v1/canvases/assets/zip', {
    method: 'POST',
    json: { filename, items },
  });
  return response.blob();
}

/** Dispatch one multi-segment film task; returns its task id. */
export async function dispatchTimelineRun(
  canvasId: string,
  req: TimelineRunRequest,
): Promise<string> {
  const response = await apiFetch(`/api/v1/canvases/${canvasId}/timeline-runs`, {
    method: 'POST',
    json: req,
  });
  const body = (await response.json()) as { data?: { task_id?: string } };
  const taskId = body.data?.task_id;
  if (!taskId) throw new Error('timeline dispatch returned no task id');
  return taskId;
}

/** IC 放大: jimeng image_upscale on a durable generation → new gen url. */
export async function upscaleGeneration(
  genId: string,
  resolution: '2k' | '4k' = '2k',
): Promise<{ id: string; url: string }> {
  const res = await apiFetch(`/api/v1/generated-media/${genId}/upscale`, {
    method: 'POST',
    json: { resolution },
  });
  const body = (await res.json()) as { data?: { id: string; url: string } };
  if (!body.data?.url) throw new Error('upscale returned no url');
  return body.data;
}
