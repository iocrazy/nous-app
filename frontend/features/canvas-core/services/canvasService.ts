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
