import { apiClient } from './apiClient';
import {
  ScriptProject,
  ScriptChapter,
  ScriptProjectSummary,
  ScriptAsset,
  ScriptAssetType,
} from '../types';

interface Envelope<T> {
  success?: boolean;
  data: T;
}

// ─── Script Project CRUD ─────────────────────────────────────────────────────

export async function fetchScriptProjects(
  projectId: string,
  page = 1,
  limit = 20,
): Promise<{ data: ScriptProjectSummary[]; total: number }> {
  const result = await apiClient.get<
    Envelope<{ items: ScriptProjectSummary[]; total: number }>
  >('/api/v1/scripts/projects', {
    query: { project_id: projectId, page, limit },
  });
  return {
    data: result.data?.items ?? [],
    total: result.data?.total ?? 0,
  };
}

export interface ScriptProjectFull extends ScriptProject {
  chapters: ScriptChapter[];
}

export async function fetchScriptProject(
  scriptId: string,
): Promise<ScriptProjectFull> {
  const result = await apiClient.get<
    Envelope<{ project: ScriptProject; chapters: ScriptChapter[] }>
  >(`/api/v1/scripts/projects/${scriptId}`);
  return { ...result.data.project, chapters: result.data.chapters ?? [] };
}

export async function createScriptProject(data: {
  project_id: string;
  name: string;
  description?: string;
}): Promise<ScriptProject> {
  const result = await apiClient.post<Envelope<ScriptProject>>(
    '/api/v1/scripts/projects',
    data,
  );
  return result.data;
}

export async function updateScriptProject(
  scriptId: string,
  data: Partial<Pick<ScriptProject, 'name' | 'description' | 'status' | 'settings_json'>>,
): Promise<ScriptProject> {
  const result = await apiClient.put<Envelope<ScriptProject>>(
    `/api/v1/scripts/projects/${scriptId}`,
    data,
  );
  return result.data;
}

export async function deleteScriptProject(scriptId: string): Promise<void> {
  await apiClient.delete(`/api/v1/scripts/projects/${scriptId}`);
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
  const result = await apiClient.post<Envelope<{ chapters: ScriptChapter[] }>>(
    `/api/v1/scripts/projects/${scriptId}/canvas/sync`,
    syncRequest,
  );
  return result.data;
}

// ─── Viewport ────────────────────────────────────────────────────────────────

export async function updateScriptViewport(
  scriptId: string,
  viewport: { x: number; y: number; zoom: number },
): Promise<void> {
  await apiClient.patch(
    `/api/v1/scripts/projects/${scriptId}/viewport`,
    viewport,
  );
}

// ─── AI Operations ───────────────────────────────────────────────────────────

export async function generateOutline(data: {
  script_id: string;
  premise: string;
  chapter_count: number;
  style_guide?: string;
}): Promise<{ task_id: string }> {
  const result = await apiClient.post<Envelope<{ task_id: string }>>(
    '/api/v1/scripts/generate-outline',
    data,
  );
  return result.data;
}

export async function expandChapter(data: {
  script_id: string;
  chapter_id: string;
  title: string;
  summary: string;
  context?: string;
}): Promise<{ content: string; chapter: Record<string, unknown> }> {
  const result = await apiClient.post<
    Envelope<{ content: string; chapter: Record<string, unknown> }>
  >('/api/v1/scripts/expand-chapter', data);
  return result.data;
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
  const result = await apiClient.post<Envelope<{ branches: ScriptChapter[] }>>(
    '/api/v1/scripts/create-branches',
    data,
  );
  return result.data;
}

export async function convertToStoryboard(data: {
  script_id: string;
  chapter_id: string;
  storyboard_project_id?: string;
}): Promise<{ task_id?: string; nodes?: Record<string, unknown>[] }> {
  const result = await apiClient.post<
    Envelope<{ task_id?: string; nodes?: Record<string, unknown>[] }>
  >('/api/v1/scripts/convert-to-storyboard', data);
  return result.data;
}

// ─── Script Assets ───────────────────────────────────────────────────────────

export async function fetchScriptAssets(
  scriptId: string,
  assetType?: ScriptAssetType,
): Promise<ScriptAsset[]> {
  const result = await apiClient.get<Envelope<ScriptAsset[]>>(
    `/api/v1/scripts/projects/${scriptId}/assets`,
    { query: { asset_type: assetType } },
  );
  return result.data;
}

export async function createScriptAsset(data: {
  script_id: string;
  asset_type: ScriptAssetType;
  name: string;
  content?: string;
}): Promise<ScriptAsset> {
  const result = await apiClient.post<Envelope<ScriptAsset>>(
    `/api/v1/scripts/projects/${data.script_id}/assets`,
    data,
  );
  return result.data;
}

export async function updateScriptAsset(
  assetId: string,
  data: Partial<Pick<ScriptAsset, 'name' | 'content' | 'data_json' | 'sort_order'>>,
): Promise<ScriptAsset> {
  const result = await apiClient.put<Envelope<ScriptAsset>>(
    `/api/v1/scripts/projects/assets/${assetId}`,
    data,
  );
  return result.data;
}

export async function deleteScriptAsset(assetId: string): Promise<void> {
  await apiClient.delete(`/api/v1/scripts/projects/assets/${assetId}`);
}
