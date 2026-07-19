import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { handleResponse, unwrapResponse } from '../utils/apiHelpers';
import { ScriptProject, ScriptChapter, ScriptProjectSummary } from '../types';

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
  // Auto-provision: bind the new script to an episode atomically. When set,
  // the backend is idempotent (returns the episode's existing active script
  // instead of a duplicate), collapsing the old create-then-bind two-call
  // dance that raced into duplicate scripts (#1432).
  episode_id?: string;
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
  data: Partial<
    Pick<
      ScriptProject,
      'name' | 'description' | 'status' | 'settings_json' | 'episode_id' | 'target_duration_sec'
    >
  >,
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

// ─── AI Operations (async) ───────────────────────────────────────────────────
// These endpoints dispatch a DBOS workflow and return a FLAT
// `{ success, task_id }` envelope (the async-dispatch convention shared by
// aiService / sb AI / resource AI) — task_id is TOP-LEVEL, not under `data`,
// so they use handleResponse (raw json), NOT unwrapResponse (which reads
// `.data` and would yield undefined → 'cannot destructure task_id').
// The LLM work happens in the background. Callers watch task_tracking via
// `useTaskCompletion(task_id, …)` and reload the script from the server on
// completion. See:
//   docs/superpowers/specs/2026-06-09-async-script-ai-design.md

export async function expandChapter(data: {
  script_id: string;
  chapter_id: string;
  title: string;
  summary: string;
  expansion_request?: string;
  context?: string;
}): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/expand-chapter`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<{ task_id: string }>(res);
}

export async function createBranches(data: {
  script_id: string;
  chapter_id: string;
  title: string;
  summary: string;
  branch_count: number;
  branch_type: 'choice' | 'condition';
  context?: string;
}): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/create-branches`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<{ task_id: string }>(res);
}
