/**
 * API client for the v2 editor: episodes / scenes / anchored element ops.
 *
 * Data endpoints use the { success, data } envelope (unwrapResponse). The
 * ops endpoint speaks an If-Match optimistic-concurrency contract (PR #1052,
 * v0.25.126): the caller's expected content_version rides in the `If-Match`
 * header and the server answers 409 (version_conflict) / 422 (op rejected)
 * with typed bodies we surface as VersionConflictError / OpRejectedError.
 * convert-to-scenes is an async dispatch that returns a FLAT
 * { success, task_id } envelope (handleResponse), not { data } (#1019).
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from '../services/parserService';
import { handleResponse, unwrapResponse } from '../utils/apiHelpers';
import type { ElementOp, ScriptElement, SceneDoc } from './types';

const apiBase = () => `${getApiUrl()}/api/v1`;

/** 409 from the ops endpoint: someone else advanced the scene. */
export class VersionConflictError extends Error {
  constructor(
    public currentVersion: number,
    public elements: ScriptElement[],
  ) {
    super('version_conflict');
    this.name = 'VersionConflictError';
  }
}

/** 422 from the ops endpoint: an op could not be applied (e.g. missing_anchor). */
export class OpRejectedError extends Error {
  constructor(
    public code: string,
    public detail: unknown,
  ) {
    super(code);
    this.name = 'OpRejectedError';
  }
}

/**
 * Backend scene ROWS carry the element list as `content_json` (only the
 * elements/ops endpoint responds with `elements`). Normalize every row into
 * the SceneDoc shape here so no component ever sees the raw column name —
 * caught live on the first real-device pass (mocked fixtures used SceneDoc
 * directly and could never surface the mismatch).
 */
type SceneRow = Omit<SceneDoc, 'elements'> & {
  elements?: ScriptElement[] | null;
  content_json?: ScriptElement[] | null;
};

function toSceneDoc(row: SceneRow): SceneDoc {
  const { content_json, elements, ...rest } = row;
  return { ...rest, elements: elements ?? content_json ?? [] };
}

export async function listScenes(scriptId: string): Promise<SceneDoc[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/scenes`, { headers });
  const rows = await unwrapResponse<SceneRow[]>(res);
  return rows.map(toSceneDoc);
}

export async function getScene(sceneId: string): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}`, { headers });
  return toSceneDoc(await unwrapResponse<SceneRow>(res));
}

export async function createScene(
  scriptId: string,
  data: Partial<SceneDoc>,
): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/scenes`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return toSceneDoc(await unwrapResponse<SceneRow>(res));
}

export async function updateSceneMeta(
  sceneId: string,
  data: Partial<
    Pick<
      SceneDoc,
      'heading_int_ext' | 'location_text' | 'time_of_day' | 'position_x' | 'position_y'
    >
  >,
): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return toSceneDoc(await unwrapResponse<SceneRow>(res));
}

export async function deleteScene(sceneId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/scenes/${sceneId}`, { method: 'DELETE', headers });
}

export async function applyOps(
  sceneId: string,
  ops: ElementOp[],
  expectedVersion: number,
): Promise<{ content_version: number; elements: ScriptElement[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/elements/ops`, {
    method: 'POST',
    headers: {
      ...headers,
      'Content-Type': 'application/json',
      'If-Match': String(expectedVersion),
    },
    body: JSON.stringify({ ops }),
  });
  if (res.status === 409) {
    const body = await res.json();
    throw new VersionConflictError(body.current_version, body.elements);
  }
  if (res.status === 422) {
    const body = await res.json();
    throw new OpRejectedError(body.code, body.detail);
  }
  return unwrapResponse<{ content_version: number; elements: ScriptElement[] }>(res);
}

export async function moveScene(
  sceneId: string,
  args: {
    chapter_id?: string | null;
    before_scene_id?: string | null;
    after_scene_id?: string | null;
  },
): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/move`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return unwrapResponse<SceneDoc>(res);
}

/**
 * An episode row. `script_count` (non-deleted scripts pointing at it) is
 * present on list responses so the UI can disable deleting a non-empty episode
 * — the episode_id FK is ON DELETE RESTRICT.
 */
export interface Episode {
  id: string;
  title: string;
  sort_order: number;
  script_count?: number;
}

export async function listEpisodes(projectId: string): Promise<Episode[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/projects/${projectId}/episodes`, { headers });
  return unwrapResponse<Episode[]>(res);
}

export async function createEpisode(
  projectId: string,
  data: { title?: string; sort_order?: number } = {},
): Promise<Episode> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/projects/${projectId}/episodes`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Episode>(res);
}

export async function updateEpisode(
  episodeId: string,
  data: { title?: string; sort_order?: number },
): Promise<Episode> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/episodes/${episodeId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Episode>(res);
}

export async function deleteEpisode(episodeId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/episodes/${episodeId}`, { method: 'DELETE', headers });
}

export async function convertToScenes(
  scriptId: string,
  chapterId: string,
): Promise<string> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${apiBase()}/scripts/${scriptId}/chapters/${chapterId}/convert-to-scenes`,
    { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' } },
  );
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return body.task_id;
}

/** Client-generated element id so anchored ops can reference brand-new rows. */
export function newElementId(): string {
  return 'el_' + crypto.randomUUID().replace(/-/g, '').slice(0, 8);
}

// ---------------------------------------------------------------------------
// Shots (Phase B P3 — the storyboard tier that hangs off a scene)
// ---------------------------------------------------------------------------

/** A shot's status machine (server-owned; never client-set through updateShot). */
export type ShotStatus = 'empty' | 'generating' | 'done' | 'failed';

/**
 * A storyboard shot row. Every id is a string end-to-end (Snowflake bigint —
 * never Number()-coerced). The parameter tags + `description` are the only
 * client-editable fields via `updateShot`; `status` and the produced media URLs
 * flow exclusively through the generate workflow's write lane.
 */
export interface Shot {
  id: string;
  scene_id: string;
  shot_number: number | null;
  shot_type: string | null;
  camera_angle: string | null;
  camera_movement: string | null;
  focal_length: string | null;
  lighting: string | null;
  description: string | null;
  image_url: string | null;
  thumbnail_url: string | null;
  video_url: string | null;
  status: ShotStatus;
  sort_order: number;
}

/** Fields a caller may set when creating or editing a shot's tags/description. */
export type ShotInput = Partial<
  Pick<
    Shot,
    | 'shot_number'
    | 'shot_type'
    | 'camera_angle'
    | 'camera_movement'
    | 'focal_length'
    | 'lighting'
    | 'description'
    | 'sort_order'
  >
>;

export async function listShots(sceneId: string): Promise<Shot[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/shots`, { headers });
  return unwrapResponse<Shot[]>(res);
}

export async function createShot(sceneId: string, data: ShotInput = {}): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/shots`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Shot>(res);
}

export async function updateShot(shotId: string, data: ShotInput): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Shot>(res);
}

export async function deleteShot(shotId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/shots/${shotId}`, { method: 'DELETE', headers });
}

export async function moveShot(
  shotId: string,
  args: { before_shot_id?: string | null; after_shot_id?: string | null },
): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}/move`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return unwrapResponse<Shot>(res);
}

/**
 * Dispatch the async AI storyboard breakdown of a scene. Returns the FLAT
 * { success, task_id } envelope (handleResponse) — the shots land some seconds
 * later, observed by polling `listShots` (#1019 flat dispatch contract).
 */
export async function autoStoryboard(sceneId: string): Promise<string> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/auto-storyboard`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
  });
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return body.task_id;
}
