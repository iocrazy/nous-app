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
import type {
  CanvasGenerationCapability,
  CanvasGenerationDispatch,
  CanvasGenerationTask,
  CanvasModelOption,
  CanvasTimelineDispatch,
  Envelope,
} from '../../../types/api';
import { isPlatformModelAvailable } from '../../../utils/platformModel';

/** An image/video catalog row for the composer picker — the backend's public
 *  projection (`CanvasModelOption`): no credential, host or provider name
 *  (2026-08-14 leak tripwire); `is_local` is the one bit derived from the
 *  provider. `type` is `'image' | 'video'` on this endpoint. */
export type GenerationModel = CanvasModelOption;

export interface GenerationDispatchRequest {
  node_id: string;
  kind: 'image' | 'video';
  prompt: string;
  model?: string;
  count?: number;
  params?: Record<string, unknown>;
  source_url?: string | null;
}

/** The keys the generation workflows write into `task_tracking.metadata`.
 *  The backend declares that column as open jsonb (`CanvasGenerationTask`),
 *  so this is the one place the known keys are named; every key stays
 *  optional because an older row may predate it. */
export interface GenerationTaskMetadata {
  result_url?: string;
  /** A JSON number from a fresh registration, a string when the daemon had
   *  already registered the file (the `existing_gen_id` branch). Stringify
   *  at use. */
  generated_media_id?: number | string;
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
}

/** `GET /canvases/generations/{task_id}` payload with its open `metadata`
 *  narrowed to the keys above. `phase` is null until the engine picks the
 *  task up; `metadata` is null on a row nothing has decorated yet. */
export type GenerationTask = Omit<CanvasGenerationTask, 'metadata'> & {
  metadata: GenerationTaskMetadata | null;
};

const TERMINAL_PHASES = new Set(['completed', 'failed', 'cancelled', 'lost']);
/** Infinite polls image tasks every 2s for up to 30min — same envelope. */
const DEFAULT_POLL_INTERVAL_MS = 2000;
const DEFAULT_POLL_TIMEOUT_MS = 30 * 60 * 1000;

export async function listGenerationModels(): Promise<GenerationModel[]> {
  const response = await apiFetch('/api/v1/canvases/generation-models');
  const body = (await response.json()) as Partial<Envelope<GenerationModel[]>>;
  if (!body.success || !Array.isArray(body.data)) {
    throw new ApiError('generation-models response missing data', 500);
  }
  // Same availability rule as Settings (utils/platformModel). The backend
  // already drops failed rows here; filtering again keeps the two pickers on
  // one predicate should that server filter ever change.
  return body.data.filter(isPlatformModelAvailable);
}

/** What one catalog model can actually honour, as projected by the backend
 *  (`GET /api/v1/canvases/generation-capabilities`). The UI hides a knob only
 *  when a model is present here and says no — absence means "unknown", never
 *  "unsupported". `honours_ratio` is deliberately absent: it names an internal
 *  strategy, not something the UI can act on. `quality_tiers` runs low→max
 *  (the router's `QUALITY_TIER_ORDER`), `[]` whenever no tier is honoured. */
export type ModelCapabilities = CanvasGenerationCapability;

/** Per-model capabilities keyed by catalog model name. Visibility matches
 *  `listGenerationModels` row for row (both endpoints read one server-side
 *  predicate), so every model the picker offers has an entry here. */
export async function listGenerationCapabilities(): Promise<Record<string, ModelCapabilities>> {
  const response = await apiFetch('/api/v1/canvases/generation-capabilities');
  const body = (await response.json()) as Partial<
    Envelope<Record<string, ModelCapabilities>>
  >;
  if (!body.success || !body.data || typeof body.data !== 'object' || Array.isArray(body.data)) {
    throw new ApiError('generation-capabilities response missing data', 500);
  }
  return body.data;
}

/** An enabled `llm` catalog row for the text prompt's model picker. Same
 *  public projection as GenerationModel — the two come from the same
 *  `nous_models` table, differing only in `type`. It never carries
 *  `actual_provider` (the old hand-written type claimed it always did). */
export type TextModel = CanvasModelOption;

/** Enabled llm models from the platform catalog (public columns only) — the
 *  single source of truth for the text prompt node's model dropdown, replacing
 *  the old hardcoded PROVIDER_OPTIONS that named models the platform doesn't
 *  carry (the 2026-07-12 "default prompt won't run" root cause). */
export async function listTextModels(): Promise<TextModel[]> {
  const response = await apiFetch('/api/v1/canvases/text-models');
  const body = (await response.json()) as Partial<Envelope<TextModel[]>>;
  if (!body.success || !Array.isArray(body.data)) {
    throw new ApiError('text-models response missing data', 500);
  }
  // text-models does not filter failed rows server-side; this is the filter.
  return body.data.filter(isPlatformModelAvailable);
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
  const body = (await response.json()) as Partial<CanvasGenerationDispatch>;
  if (!body.success || !Array.isArray(body.task_ids)) {
    throw new ApiError('generation dispatch response missing task_ids', 500);
  }
  return body.task_ids;
}

export async function getGeneration(taskId: string): Promise<GenerationTask> {
  const response = await apiFetch(`/api/v1/canvases/generations/${taskId}`);
  const body = (await response.json()) as Partial<Envelope<GenerationTask>>;
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
  const body = (await response.json()) as Partial<Envelope<CanvasTimelineDispatch>>;
  const taskId = body.data?.task_id;
  if (!taskId) throw new Error('timeline dispatch returned no task id');
  return taskId;
}

/** Typed 5xx bodies the upscale route emits under `details` (the backend
 * passes them through the 5xx scrub via TYPED_5XX_CODES). */
type UpscaleFailureDetails =
  | {
      code: 'upscale_backend_failed';
      provider: string | null;
      upstream_status: number | null;
      upstream_code: string | null;
    }
  | { code: 'upscale_unavailable'; reason: string };

/** Turn a typed upscale refusal into a message that names the cause — "key
 * not authorised" (model_not_found), quota, and engine-not-ready need
 * different fixes. Anything untyped keeps the original error. */
function upscaleError(err: unknown): unknown {
  if (!(err instanceof ApiError)) return err;
  const details = err.details as Partial<UpscaleFailureDetails> | null | undefined;
  if (details?.code === 'upscale_backend_failed') {
    const d = details as Extract<UpscaleFailureDetails, { code: 'upscale_backend_failed' }>;
    const cause = d.upstream_code ?? `HTTP ${d.upstream_status ?? err.status}`;
    return new Error(`${d.provider ?? 'upscale backend'}: ${cause}`);
  }
  if (details?.code === 'upscale_unavailable') {
    return new Error('no upscale backend enabled');
  }
  return err;
}

/** IC 放大: super-resolve a durable generation (nous-engine first, dreamina
 * CLI fallback — chosen server-side) → new gen url. */
export async function upscaleGeneration(
  genId: string,
  resolution: '2k' | '4k' = '2k',
): Promise<{ id: string; url: string }> {
  let res: Response;
  try {
    res = await apiFetch(`/api/v1/generated-media/${genId}/upscale`, {
      method: 'POST',
      json: { resolution },
    });
  } catch (err) {
    throw upscaleError(err);
  }
  const body = (await res.json()) as { data?: { id: string; url: string } };
  if (!body.data?.url) throw new Error('upscale returned no url');
  return body.data;
}
