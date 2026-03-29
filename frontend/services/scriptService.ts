import { getAuthHeaders } from './parserService';
import { ScriptProject, ScriptChapter, ScriptProjectSummary } from '../types';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

async function unwrapResponse<T>(res: Response): Promise<T> {
  const body = await handleResponse<{ success: boolean; data: T }>(res);
  return body.data;
}

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
