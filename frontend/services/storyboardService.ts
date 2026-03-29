import { getAuthHeaders } from './parserService';
import {
  StoryboardProject,
  StoryboardNode,
  StoryboardEdge,
  StoryboardFrame,
  StoryboardCharacter,
  ProjectSummary,
} from '../types';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

// ─── Response helpers ─────────────────────────────────────────────────────────

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${text}`);
  }
  if (res.status === 204) {
    return undefined as unknown as T;
  }
  return res.json() as Promise<T>;
}

/**
 * Unwrap the backend envelope `{ success, data: T }` → `T`.
 * All storyboard API endpoints return this envelope format.
 */
async function unwrapResponse<T>(res: Response): Promise<T> {
  const body = await handleResponse<{ success: boolean; data: T }>(res);
  return body.data;
}

// ─── Project CRUD ─────────────────────────────────────────────────────────────

export async function fetchProjects(
  teamId: string,
  page = 1,
  limit = 20,
  projectId?: string
): Promise<{ data: ProjectSummary[]; total: number }> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({ team_id: teamId, page: String(page), limit: String(limit) });
  if (projectId) {
    params.set('project_id', projectId);
  }
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects?${params}`, { headers });
  const result = await unwrapResponse<{ items: ProjectSummary[]; total: number }>(res);
  return { data: result?.items ?? [], total: result?.total ?? 0 };
}

export interface ProjectFull extends StoryboardProject {
  nodes: StoryboardNode[];
  edges: StoryboardEdge[];
  characters: StoryboardCharacter[];
}

export async function fetchProject(projectId: string): Promise<ProjectFull> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}`, { headers });
  const raw = await unwrapResponse<{ project: StoryboardProject; nodes: StoryboardNode[]; edges: StoryboardEdge[]; characters: StoryboardCharacter[] }>(res);
  return {
    ...raw.project,
    nodes: raw.nodes ?? [],
    edges: raw.edges ?? [],
    characters: raw.characters ?? [],
  };
}

export async function createProject(data: {
  team_id: string;
  name: string;
  description?: string;
  project_id?: string;
}): Promise<StoryboardProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  return unwrapResponse<StoryboardProject>(res);
}

export async function updateProject(
  projectId: string,
  data: Partial<Pick<StoryboardProject, 'name' | 'description' | 'cover_image_url' | 'settings_json' | 'status'>>
): Promise<StoryboardProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(data),
  });
  return unwrapResponse<StoryboardProject>(res);
}

export async function deleteProject(projectId: string): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}`, {
    method: 'DELETE',
    headers,
  });
  return unwrapResponse<void>(res);
}

export async function updateViewport(
  projectId: string,
  viewport: { x: number; y: number; zoom: number }
): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}/viewport`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify({ viewport_json: viewport }),
  });
  return unwrapResponse<void>(res);
}

// ─── Canvas Sync ──────────────────────────────────────────────────────────────

export interface CanvasSyncData {
  nodes: Array<Pick<StoryboardNode, 'id' | 'position_x' | 'position_y' | 'width' | 'height' | 'data_json' | 'sort_order' | 'locked'>>;
  edges: Array<Pick<StoryboardEdge, 'id' | 'source_node_id' | 'target_node_id' | 'source_handle' | 'target_handle' | 'edge_type'>>;
  deleted_node_ids?: string[];
  deleted_edge_ids?: string[];
}

export interface SyncResult {
  synced_nodes: number;
  synced_edges: number;
  deleted_nodes: number;
  deleted_edges: number;
}

export async function syncCanvas(
  projectId: string,
  syncData: CanvasSyncData
): Promise<SyncResult> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}/sync`, {
    method: 'POST',
    headers,
    body: JSON.stringify(syncData),
  });
  return unwrapResponse<SyncResult>(res);
}

// ─── Characters ───────────────────────────────────────────────────────────────

export async function fetchCharacters(projectId: string): Promise<StoryboardCharacter[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}/characters`, { headers });
  return unwrapResponse<StoryboardCharacter[]>(res);
}

export async function createCharacter(
  projectId: string,
  data: { name: string; description?: string; visual_traits?: Record<string, string> }
): Promise<StoryboardCharacter> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}/characters`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  return unwrapResponse<StoryboardCharacter>(res);
}

export async function updateCharacter(
  characterId: string,
  data: Partial<Pick<StoryboardCharacter, 'name' | 'description' | 'reference_image_url' | 'thumbnail_url' | 'visual_traits' | 'sort_order'>>
): Promise<StoryboardCharacter> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/characters/${characterId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(data),
  });
  return unwrapResponse<StoryboardCharacter>(res);
}

export async function deleteCharacter(characterId: string): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/characters/${characterId}`, {
    method: 'DELETE',
    headers,
  });
  return unwrapResponse<void>(res);
}

// ─── Frames ───────────────────────────────────────────────────────────────────

export async function updateFrame(
  frameId: string,
  data: Partial<Pick<StoryboardFrame, 'note' | 'shot_type' | 'camera_angle' | 'camera_movement' | 'focal_length' | 'lighting' | 'duration_seconds' | 'transition_type' | 'annotations_json' | 'sort_order'>>
): Promise<StoryboardFrame> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/frames/${frameId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(data),
  });
  return unwrapResponse<StoryboardFrame>(res);
}

export async function reorderFrames(frameIds: string[]): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/frames/reorder`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ frame_ids: frameIds }),
  });
  return unwrapResponse<void>(res);
}

// ─── AI ───────────────────────────────────────────────────────────────────────

export interface GenerateImageParams {
  project_id: string;
  node_id?: string;
  frame_id?: string;
  prompt: string;
  provider?: string;
  style?: string;
  reference_character_ids?: string[];
}

export async function generateImage(data: GenerateImageParams): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/generate/image`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  // Backend returns { success, task_id } directly (not wrapped in data)
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return { task_id: body.task_id };
}

export interface GenerateVideoParams {
  project_id: string;
  node_id?: string;
  image_url: string;
  prompt?: string;
  duration_seconds?: number;
  provider?: string;
}

export async function generateVideo(data: GenerateVideoParams): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/generate/video`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return { task_id: body.task_id };
}

export interface SplitScriptParams {
  project_id: string;
  script_text: string;
  target_frames?: number;
  style?: string;
}

export async function splitScript(data: SplitScriptParams): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/split-script`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return { task_id: body.task_id };
}

export async function chatWithAI(
  projectId: string,
  message: string,
  frameId?: string
): Promise<{ response: string; actions: unknown[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, frame_id: frameId }),
  });
  return unwrapResponse<{ response: string; actions: unknown[] }>(res);
}

// ─── Image Upload ─────────────────────────────────────────────────────────────

export interface UploadImageResult {
  asset_id: string;
  image_url: string;
  preview_url: string;
  width: number;
  height: number;
  file_hash: string;
}

export async function uploadImage(
  projectId: string,
  file: File,
  nodeId?: string,
): Promise<UploadImageResult> {
  const headers = await getAuthHeaders();
  // Remove Content-Type — let the browser set multipart boundary automatically
  delete (headers as Record<string, string>)['Content-Type'];

  const formData = new FormData();
  formData.append('file', file);
  if (nodeId) {
    formData.append('node_id', nodeId);
  }

  const res = await fetch(
    `${getApiUrl()}/api/v1/storyboard/projects/${projectId}/upload`,
    {
      method: 'POST',
      headers,
      body: formData,
    },
  );
  return unwrapResponse<UploadImageResult>(res);
}

// ─── Image Split ──────────────────────────────────────────────────────────────

export interface SplitImageFrame {
  id: string;
  asset_id: string;
  frame_index: number;
  image_url: string;
  preview_url: string;
  width: number;
  height: number;
  row: number;
  col: number;
  node_id?: string;
  sort_order: number;
}

export interface SplitImageResult {
  frames: SplitImageFrame[];
  source_asset_id: string;
  rows: number;
  cols: number;
}

export async function splitImage(
  projectId: string,
  assetId: string,
  rows: number,
  cols: number,
  nodeId?: string,
): Promise<SplitImageResult> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${getApiUrl()}/api/v1/storyboard/projects/${projectId}/split-image`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({
        asset_id: assetId,
        rows,
        cols,
        node_id: nodeId,
      }),
    },
  );
  return unwrapResponse<SplitImageResult>(res);
}

// ─── Export ───────────────────────────────────────────────────────────────────

export type ExportFormat = 'png' | 'pdf' | 'zip';

export interface ExportProjectOptions {
  includeFrameNumbers?: boolean;
  includeAnnotations?: boolean;
  includeCameraOverlays?: boolean;
  includeNotes?: boolean;
  includeMetadata?: boolean;
  columns?: number;
  paperSize?: 'a4' | 'letter' | 'custom';
  includeCharacterPage?: boolean;
  quality?: 'low' | 'medium' | 'high';
  includeAllAssets?: boolean;
}

export async function exportProject(
  projectId: string,
  format: ExportFormat,
  options?: ExportProjectOptions
): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}/export`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ format, options: options ?? {} }),
  });
  return unwrapResponse<{ task_id: string }>(res);
}
