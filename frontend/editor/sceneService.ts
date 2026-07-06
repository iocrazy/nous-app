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

export async function listScenes(scriptId: string): Promise<SceneDoc[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/scenes`, { headers });
  return unwrapResponse<SceneDoc[]>(res);
}

export async function getScene(sceneId: string): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}`, { headers });
  return unwrapResponse<SceneDoc>(res);
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
  return unwrapResponse<SceneDoc>(res);
}

export async function updateSceneMeta(
  sceneId: string,
  data: Partial<Pick<SceneDoc, 'heading_int_ext' | 'location_text' | 'time_of_day'>>,
): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<SceneDoc>(res);
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

export async function listEpisodes(
  projectId: string,
): Promise<{ id: string; title: string; sort_order: number }[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/projects/${projectId}/episodes`, { headers });
  return unwrapResponse<{ id: string; title: string; sort_order: number }[]>(res);
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
