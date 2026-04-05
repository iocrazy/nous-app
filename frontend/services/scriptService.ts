import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { handleResponse, unwrapResponse } from '../utils/apiHelpers';
import { ScriptProject, ScriptChapter, ScriptProjectSummary, ScriptAsset, ScriptAssetType } from '../types';

// ─── Script Project CRUD ─────────────────────────────────────────────────────

export async function fetchScriptProjects(
  projectId: string,
  page = 1,
  limit = 20,
): Promise<{ data: ScriptProjectSummary[]; total: number }> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({
    project_id: projectId,
    page: String(page),
    limit: String(limit),
  });
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects?${params}`, { headers });
  const result = await unwrapResponse<{ items: ScriptProjectSummary[]; total: number }>(res);
  return { data: result?.items ?? [], total: result?.total ?? 0 };
}

export interface ScriptProjectFull extends ScriptProject {
  chapters: ScriptChapter[];
}

export async function fetchScriptProject(scriptId: string): Promise<ScriptProjectFull> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}`, { headers });
  const raw = await unwrapResponse<{ project: ScriptProject; chapters: ScriptChapter[] }>(res);
  return { ...raw.project, chapters: raw.chapters ?? [] };
}

export async function createScriptProject(data: {
  project_id: string;
  name: string;
  description?: string;
}): Promise<ScriptProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<ScriptProject>(res);
}

export async function updateScriptProject(
  scriptId: string,
  data: Partial<Pick<ScriptProject, 'name' | 'description' | 'status' | 'settings_json'>>,
): Promise<ScriptProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<ScriptProject>(res);
}

export async function deleteScriptProject(scriptId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}`, {
    method: 'DELETE',
    headers,
  });
}

// ─── Canvas Sync ─────────────────────────────────────────────────────────────

export async function syncScriptCanvas(
  scriptId: string,
  syncRequest: {
    added_chapters: Partial<ScriptChapter>[];
    updated_chapters: Partial<ScriptChapter>[];
    deleted_chapter_ids: string[];
  },
): Promise<{ chapters: ScriptChapter[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}/canvas/sync`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(syncRequest),
  });
  return unwrapResponse<{ chapters: ScriptChapter[] }>(res);
}

// ─── Viewport ────────────────────────────────────────────────────────────────

export async function updateScriptViewport(
  scriptId: string,
  viewport: { x: number; y: number; zoom: number },
): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}/viewport`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(viewport),
  });
}

// ─── AI Operations ───────────────────────────────────────────────────────────

export async function generateOutline(data: {
  script_id: string;
  premise: string;
  chapter_count: number;
  genre?: string;
  style_guide?: string;
}): Promise<{ task_id: string; outline?: { name: string; chapters: Array<{ title: string; summary: string }> } }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/generate-outline`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<{ task_id: string; outline?: { name: string; chapters: Array<{ title: string; summary: string }> } }>(res);
}

export async function expandChapter(data: {
  script_id: string;
  chapter_id: string;
  title: string;
  summary: string;
  expansion_request?: string;
  context?: string;
}): Promise<{ content: string; chapter: Record<string, unknown> }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/expand-chapter`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<{ content: string; chapter: Record<string, unknown> }>(res);
}

export async function createBranches(data: {
  script_id: string;
  chapter_id: string;
  title: string;
  summary: string;
  branch_count: number;
  branch_type: 'choice' | 'condition';
  context?: string;
}): Promise<{ branches: ScriptChapter[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/create-branches`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<{ branches: ScriptChapter[] }>(res);
}

export async function convertToStoryboard(data: {
  script_id: string;
  chapter_id: string;
  storyboard_project_id?: string;
}): Promise<{ task_id?: string; nodes?: Record<string, unknown>[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/convert-to-storyboard`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<{ task_id?: string; nodes?: Record<string, unknown>[] }>(res);
}

// ─── Script Assets ───────────────────────────────────────────────────────────

export async function fetchScriptAssets(
  scriptId: string,
  assetType?: ScriptAssetType,
): Promise<ScriptAsset[]> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams();
  if (assetType) params.set('asset_type', assetType);
  const res = await fetch(
    `${getApiUrl()}/api/v1/scripts/projects/${scriptId}/assets?${params}`,
    { headers },
  );
  return unwrapResponse<ScriptAsset[]>(res);
}

export async function createScriptAsset(data: {
  script_id: string;
  asset_type: ScriptAssetType;
  name: string;
  content?: string;
}): Promise<ScriptAsset> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${getApiUrl()}/api/v1/scripts/projects/${data.script_id}/assets`,
    {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  return unwrapResponse<ScriptAsset>(res);
}

export async function updateScriptAsset(
  assetId: string,
  data: Partial<Pick<ScriptAsset, 'name' | 'content' | 'data_json' | 'sort_order'>>,
): Promise<ScriptAsset> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${getApiUrl()}/api/v1/scripts/projects/assets/${assetId}`,
    {
      method: 'PUT',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  return unwrapResponse<ScriptAsset>(res);
}

export async function deleteScriptAsset(assetId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/scripts/projects/assets/${assetId}`, {
    method: 'DELETE',
    headers,
  });
}
