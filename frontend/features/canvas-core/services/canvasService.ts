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
  CanvasKind,
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
 * Idempotent get-or-create of an episode's system storyboard canvas
 * (`kind==='storyboard'`, shot-nodes-on-canvas Task 1, mig 421). Backend:
 * `GET /api/v1/canvases/storyboard?episode_id=` — registered before the
 * dynamic `/canvases/{canvas_id}` route so 'storyboard' never gets
 * captured as an id. Every call for the same episode returns the SAME row
 * (lazy-created on first call, matching the design's "empty episode owes
 * no canvas" rule) — safe to call on every mount of the storyboard page's
 * Canvas tab.
 */
export async function getOrCreateStoryboardCanvas(episodeId: string): Promise<Canvas> {
  const response = await apiFetch('/api/v1/canvases/storyboard', {
    query: { episode_id: episodeId },
  });
  return readEnvelope<Canvas>(response);
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

// ============================================================
// Canvas derive — any image the canvas shows (2026-09-10)
// ============================================================

/** One image a canvas derive produced: a durable generated-media item. */
export interface CanvasDerivedImage {
  /** generated_media id (snowflake, string on the wire). */
  id: string;
  /** Always `/api/v1/generated-media/{id}/cover`. */
  url: string;
  kind: 'image';
  /** 0-based tile position for a grid derive; null otherwise. */
  row: number | null;
  col: number | null;
}

export interface CanvasDeriveOptions {
  /** The node the edit was made from — provenance on the registered row. */
  nodeId?: string;
}

export interface CanvasOutpaintOptions extends CanvasDeriveOptions {
  prompt?: string;
}

function canvasDerivePayload(
  sourceUrl: string,
  opts: CanvasDeriveOptions,
): Record<string, unknown> {
  const payload: Record<string, unknown> = { source_url: sourceUrl };
  if (opts.nodeId) payload.node_id = opts.nodeId;
  return payload;
}

async function postCanvasDerive(
  canvasId: string,
  op: 'crop' | 'grid' | 'outpaint',
  payload: Record<string, unknown>,
): Promise<CanvasDerivedImage[]> {
  const response = await apiFetch(`/api/v1/canvases/${canvasId}/derive-${op}`, {
    method: 'POST',
    json: payload,
  });
  const data = await readEnvelope<{ images?: CanvasDerivedImage[] }>(response);
  if (!Array.isArray(data.images) || data.images.length === 0) {
    throw new ApiError(`canvas derive-${op} returned no images`, response.status);
  }
  return data.images;
}

/**
 * Crop whatever image `sourceUrl` names — a generation, an upload, a library
 * asset — into a new generated-media item in the canvas's space. The backend
 * checks the canvas write and the source read; refusals arrive as ApiError.
 */
export async function deriveCanvasCrop(
  canvasId: string,
  sourceUrl: string,
  region: CropRegion,
  opts: CanvasDeriveOptions = {},
): Promise<CanvasDerivedImage> {
  const [image] = await postCanvasDerive(canvasId, 'crop', {
    ...canvasDerivePayload(sourceUrl, opts),
    region,
  });
  return image;
}

/** Split `sourceUrl` along normalized lines; one item per tile, row-major. */
export async function deriveCanvasGrid(
  canvasId: string,
  sourceUrl: string,
  lines: GridLines,
  opts: CanvasDeriveOptions = {},
): Promise<CanvasDerivedImage[]> {
  return postCanvasDerive(canvasId, 'grid', {
    ...canvasDerivePayload(sourceUrl, opts),
    xs: lines.xs,
    ys: lines.ys,
  });
}

/** Extend `sourceUrl`'s canvas (blur fill) into a new item. */
export async function deriveCanvasOutpaint(
  canvasId: string,
  sourceUrl: string,
  padding: OutpaintPadding,
  opts: CanvasOutpaintOptions = {},
): Promise<CanvasDerivedImage> {
  const payload: Record<string, unknown> = {
    ...canvasDerivePayload(sourceUrl, opts),
    left: padding.left,
    top: padding.top,
    right: padding.right,
    bottom: padding.bottom,
  };
  if (opts.prompt) payload.prompt = opts.prompt;
  const [image] = await postCanvasDerive(canvasId, 'outpaint', payload);
  return image;
}

// ============================================================
// Team canvas tree (canvas nav N+1 fix)
// ============================================================

export interface TeamCanvasSummary {
  id: string;
  name: string;
  kind: CanvasKind;
  updated_at: string;
}

export interface TeamCanvasProject {
  project_id: string;
  project_name: string;
  canvases: TeamCanvasSummary[];
}

/**
 * Every project in the team with its canvases as summary rows — ONE call,
 * replacing the landing page's fetchProjects + per-project listCanvases
 * fan-out (and its full-document payload).
 */
export async function listTeamCanvases(teamId: string): Promise<TeamCanvasProject[]> {
  const response = await apiFetch(`/api/v1/canvases/team/${teamId}`);
  return readEnvelope<TeamCanvasProject[]>(response);
}

// ---- Trash (Infinite parity G9) ------------------------------------------

export interface TrashedCanvas {
  id: string;
  name: string;
  kind: string;
  updated_at: string | null;
  deleted_at: string | null;
  project_id: string;
  project_name: string;
}

/** A team's trashed canvases, newest-trashed first. */
export async function listTeamCanvasTrash(teamId: string): Promise<TrashedCanvas[]> {
  const response = await apiFetch(`/api/v1/canvases/team/${teamId}/trash`);
  return readEnvelope<TrashedCanvas[]>(response);
}

/** A project's trashed canvases (workspace Trash module) — project-scoped
 *  so it works for personal projects without the team-id sentinel. */
export async function listProjectCanvasTrash(
  projectId: string,
): Promise<TrashedCanvas[]> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/canvases/trash`);
  return readEnvelope<TrashedCanvas[]>(response);
}

/** Bring a trashed canvas back to life. */
export async function restoreCanvas(canvasId: string): Promise<void> {
  await apiFetch(`/api/v1/canvases/${canvasId}/restore`, { method: 'POST' });
}

/** Permanently delete — only valid for canvases already in the trash. */
export async function purgeCanvas(canvasId: string): Promise<void> {
  await apiFetch(`/api/v1/canvases/${canvasId}/purge`, { method: 'DELETE' });
}
