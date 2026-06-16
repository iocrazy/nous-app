/**
 * Canvas REST client (Phase 1 of canvas + AI upgrade).
 *
 * Matches `backend/app/api/canvases_router.py`. The save() helper
 * surfaces the 409 conflict body as a typed result instead of throwing,
 * because the Zustand store needs the current server row to render a
 * merge prompt.
 */

import { apiFetch, ApiError, buildAuthHeaders } from '../../../services/apiClient';
import { getApiUrl } from '../../../utils/apiConfig';
import type { GridLines } from '../editor/gridMath';
import type { OutpaintPadding } from '../editor/outpaintMath';
import type { CropRegion } from '../editor/types';
import type {
  Canvas,
  CanvasCreatePayload,
  CanvasSaveResult,
  CanvasUpdatePayload,
} from '../types';

interface Envelope<T> {
  success: boolean;
  data: T;
}

async function readEnvelope<T>(response: Response): Promise<T> {
  const body = (await response.json()) as Envelope<T>;
  if (!body.success || body.data === undefined) {
    throw new ApiError('canvas service response missing data', response.status);
  }
  return body.data;
}

export async function getCanvas(canvasId: string): Promise<Canvas> {
  const response = await apiFetch(`/api/v1/canvases/${canvasId}`);
  return readEnvelope<Canvas>(response);
}

export async function listCanvases(projectId: string): Promise<Canvas[]> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/canvases`);
  return readEnvelope<Canvas[]>(response);
}

export async function createCanvas(
  projectId: string,
  payload: CanvasCreatePayload = {},
): Promise<Canvas> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/canvases`, {
    method: 'POST',
    json: payload,
  });
  return readEnvelope<Canvas>(response);
}

export async function deleteCanvas(canvasId: string): Promise<void> {
  await apiFetch(`/api/v1/canvases/${canvasId}`, { method: 'DELETE' });
}

/**
 * Save a canvas with optimistic-lock semantics.
 *
 *   { ok: true, canvas }  — saved; `canvas.base_updated_at` is the new lock token.
 *   { ok: false, conflict } — 409 from server; `conflict` is the current
 *                            server state, ready to feed back into the store
 *                            for a merge/discard UI.
 *
 * Network failures still throw — only the documented 409 path becomes
 * a typed result.
 */
export async function saveCanvas(
  canvasId: string,
  payload: CanvasUpdatePayload,
): Promise<CanvasSaveResult> {
  // We hand-roll fetch here (instead of apiFetch) so we can inspect the
  // 409 body before apiClient's error wrapper stringifies the object
  // detail into "[object Object]". The 200/4xx/5xx paths still match
  // apiFetch semantics — see ApiError below.
  const headers = await buildAuthHeaders();
  const url = `${getApiUrl()}/api/v1/canvases/${canvasId}`;
  const response = await fetch(url, {
    method: 'PUT',
    headers,
    body: JSON.stringify(payload),
  });

  if (response.status === 409) {
    const body = (await response.json()) as unknown;
    const conflict = pickCurrent(body);
    if (conflict) return { ok: false, conflict };
    // Fall through to error if the body didn't carry the expected shape.
    throw new ApiError('canvas conflict without current state', 409);
  }

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown; error?: unknown };
      const candidate = body?.error ?? body?.detail;
      if (typeof candidate === 'string') message = candidate;
    } catch {
      /* keep default message */
    }
    throw new ApiError(message, response.status);
  }

  const canvas = await readEnvelope<Canvas>(response);
  return { ok: true, canvas };
}

function pickCurrent(node: unknown): Canvas | null {
  if (!node || typeof node !== 'object') return null;
  const obj = node as Record<string, unknown>;
  if (obj.current && typeof obj.current === 'object') {
    return obj.current as Canvas;
  }
  if (obj.detail) {
    return pickCurrent(obj.detail);
  }
  return null;
}

// ============================================================
// Graph-run enqueue (Phase 6d-M4b)
// ============================================================

/** Shape of the `POST /api/v1/canvases/{id}/graph-runs` response envelope data. */
export interface GraphRunResponse {
  /** DBOS workflow id — also the task_tracking PK. Subtask rows have ids
   *  `{task_id}-node-{position}` with `metadata.node_id` set to the canvas
   *  node id. */
  task_id: string;
  dbos_workflow_id: string;
}

/**
 * Enqueue a server-side graph run for the given canvas.
 *
 * The backend starts a DBOS workflow that executes each node in the
 * provided `nodeOrder` (topologically sorted), creates a `task_tracking`
 * row per node, and persists `run_result`/`run_status` back into the
 * canvas `nodes_json`. The call returns immediately — use the returned
 * `task_id` to correlate the per-node subtask rows that arrive via the
 * existing Phase-6a Supabase Realtime subscription.
 *
 * Throws `ApiError` on any non-2xx response (400 empty order, 404 canvas
 * not found, 403 access denied, 500 backend failure).
 */
export async function enqueueGraphRun(
  canvasId: string,
  nodeOrder: string[],
  continueOnFailure: boolean,
): Promise<GraphRunResponse> {
  const response = await apiFetch(`/api/v1/canvases/${canvasId}/graph-runs`, {
    method: 'POST',
    json: {
      canvas_id: canvasId,
      node_order: nodeOrder,
      continue_on_failure: continueOnFailure,
    },
  });
  return readEnvelope<GraphRunResponse>(response);
}

// ============================================================
// Crop derive (Phase 3 Day 6)
// ============================================================

/** Shape of the resource row returned by `POST /resources/{id}/derive-crop`. */
export interface DerivedResource {
  id: string;
  filename: string;
  file_path: string;
  mime_type: string;
  file_size_bytes: number;
  // The backend response carries many more columns; only the fields
  // the crop-editor flow consumes are typed.
  [key: string]: unknown;
}

export interface DeriveCropOptions {
  /** Optional override for the new resource's filename. */
  filename?: string;
}

/**
 * Crop an existing image resource and persist the result as a new
 * sibling resource. Wraps the backend `crop_normalized` primitive +
 * the resources insert pipeline.
 *
 * Throws `ApiError` on any non-2xx response (404 source missing,
 * 400 invalid region / non-image, 403 access denied, 500 backend
 * failure). Callers should surface the message via toast / inline.
 */
export async function deriveCrop(
  sourceResourceId: string,
  region: CropRegion,
  opts: DeriveCropOptions = {},
): Promise<DerivedResource> {
  const payload: Record<string, unknown> = { region };
  if (opts.filename) payload.filename = opts.filename;
  const response = await apiFetch(
    `/api/v1/resources/${sourceResourceId}/derive-crop`,
    { method: 'POST', json: payload },
  );
  return readEnvelope<DerivedResource>(response);
}

// ============================================================
// Grid derive (Phase 3 Day 9)
// ============================================================

/** One tile of a grid derive — row/col are 0-based, row-major. */
export interface GridTileResult {
  row: number;
  col: number;
  resource: DerivedResource;
}

/** Shape of `POST /resources/{id}/derive-grid`'s data payload. */
export interface GridDeriveResult {
  rows: number;
  cols: number;
  tiles: GridTileResult[];
}

export interface DeriveGridOptions {
  /** Optional filename prefix; tiles default to grid-r{row}c{col}-{source}. */
  filenamePrefix?: string;
}

/**
 * Split an existing image resource along normalized split lines and
 * persist every tile as a new sibling resource. Wraps the backend
 * `tiles_from_lines` + crop pipeline.
 *
 * Throws `ApiError` on any non-2xx response (404 source missing,
 * 400 invalid lines / non-image, 403 access denied, 500 backend
 * failure). Callers should surface the message via toast / inline.
 */
export async function deriveGrid(
  sourceResourceId: string,
  lines: GridLines,
  opts: DeriveGridOptions = {},
): Promise<GridDeriveResult> {
  const payload: Record<string, unknown> = { xs: lines.xs, ys: lines.ys };
  if (opts.filenamePrefix) payload.filename_prefix = opts.filenamePrefix;
  const response = await apiFetch(
    `/api/v1/resources/${sourceResourceId}/derive-grid`,
    { method: 'POST', json: payload },
  );
  return readEnvelope<GridDeriveResult>(response);
}

// ============================================================
// Mask-cutout derive (Phase 3 Day 12)
// ============================================================

export interface DeriveMaskCutoutOptions {
  /** Optional override; defaults to ``cutout-{source stem}.png``. */
  filename?: string;
}

/**
 * Apply a painted mask (raw base64 PNG, white = keep) to an existing
 * image resource and persist the RGBA cutout as a new sibling
 * resource. Wraps the backend `apply_mask_cutout` primitive + the
 * shared derive pipeline.
 *
 * Throws `ApiError` on any non-2xx response (404 source missing,
 * 400 invalid / empty mask, 413 oversized mask, 403 access denied,
 * 500 backend failure).
 */
export async function deriveMaskCutout(
  sourceResourceId: string,
  maskPngBase64: string,
  opts: DeriveMaskCutoutOptions = {},
): Promise<DerivedResource> {
  const payload: Record<string, unknown> = { mask_png_base64: maskPngBase64 };
  if (opts.filename) payload.filename = opts.filename;
  const response = await apiFetch(
    `/api/v1/resources/${sourceResourceId}/derive-mask-cutout`,
    { method: 'POST', json: payload },
  );
  return readEnvelope<DerivedResource>(response);
}

// ============================================================
// Outpaint derive (Phase 3 Day 15)
// ============================================================

export interface DeriveOutpaintOptions {
  /** Collected for the future AI fill; the v1 blur fill ignores it. */
  prompt?: string;
  /** Optional override; defaults to ``outpaint-{source.filename}``. */
  filename?: string;
}

/**
 * Extend an image resource's canvas (blur-fill v1) and persist the
 * result as a new sibling resource. Wraps the backend
 * `extend_canvas` primitive + the shared derive pipeline.
 *
 * Throws `ApiError` on any non-2xx response (404 source missing,
 * 400 invalid padding / non-image, 403 access denied, 500 backend
 * failure).
 */
export async function deriveOutpaint(
  sourceResourceId: string,
  padding: OutpaintPadding,
  opts: DeriveOutpaintOptions = {},
): Promise<DerivedResource> {
  const payload: Record<string, unknown> = {
    left: padding.left,
    top: padding.top,
    right: padding.right,
    bottom: padding.bottom,
  };
  if (opts.prompt) payload.prompt = opts.prompt;
  if (opts.filename) payload.filename = opts.filename;
  const response = await apiFetch(
    `/api/v1/resources/${sourceResourceId}/derive-outpaint`,
    { method: 'POST', json: payload },
  );
  return readEnvelope<DerivedResource>(response);
}
