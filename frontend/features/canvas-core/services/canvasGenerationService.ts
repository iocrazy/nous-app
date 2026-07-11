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
  actual_provider: string;
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

/** Poll one task until it reaches a terminal phase (default 2s / 30min). */
export async function pollGeneration(
  taskId: string,
  opts: {
    intervalMs?: number;
    timeoutMs?: number;
    /** Fired on every non-terminal poll — carries the live task row. */
    onTick?: (task: GenerationTask) => void;
  } = {},
): Promise<GenerationTask> {
  const intervalMs = opts.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_POLL_TIMEOUT_MS;
  const startedAt = Date.now();
  for (;;) {
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
