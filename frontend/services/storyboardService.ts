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

// ─── Project CRUD ─────────────────────────────────────────────────────────────

export async function fetchProjects(
  teamId: string,
  page = 1,
  limit = 20
): Promise<{ data: ProjectSummary[]; total: number }> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({ team_id: teamId, page: String(page), limit: String(limit) });
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects?${params}`, { headers });
  return handleResponse<{ data: ProjectSummary[]; total: number }>(res);
}

export interface ProjectFull extends StoryboardProject {
  nodes: StoryboardNode[];
  edges: StoryboardEdge[];
  characters: StoryboardCharacter[];
}

export async function fetchProject(projectId: string): Promise<ProjectFull> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}`, { headers });
  return handleResponse<ProjectFull>(res);
}

export async function createProject(data: {
  team_id: string;
  name: string;
  description?: string;
}): Promise<StoryboardProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<StoryboardProject>(res);
}

export async function updateProject(
  projectId: string,
  data: Partial<Pick<StoryboardProject, 'name' | 'description' | 'cover_image_url' | 'settings_json' | 'status'>>
): Promise<StoryboardProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<StoryboardProject>(res);
}

export async function deleteProject(projectId: string): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}`, {
    method: 'DELETE',
    headers,
  });
  return handleResponse<void>(res);
}

export async function updateViewport(
  projectId: string,
  viewport: { x: number; y: number; zoom: number }
): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}/viewport`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify({ viewport_json: viewport }),
  });
  return handleResponse<void>(res);
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
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}/sync`, {
    method: 'POST',
    headers,
    body: JSON.stringify(syncData),
  });
  return handleResponse<SyncResult>(res);
}

// ─── Characters ───────────────────────────────────────────────────────────────

export async function fetchCharacters(projectId: string): Promise<StoryboardCharacter[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}/characters`, { headers });
  return handleResponse<StoryboardCharacter[]>(res);
}

export async function createCharacter(
  projectId: string,
  data: { name: string; description?: string; visual_traits?: Record<string, string> }
): Promise<StoryboardCharacter> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}/characters`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<StoryboardCharacter>(res);
}

export async function updateCharacter(
  characterId: string,
  data: Partial<Pick<StoryboardCharacter, 'name' | 'description' | 'reference_image_url' | 'thumbnail_url' | 'visual_traits' | 'sort_order'>>
): Promise<StoryboardCharacter> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/characters/${characterId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<StoryboardCharacter>(res);
}

export async function deleteCharacter(characterId: string): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/characters/${characterId}`, {
    method: 'DELETE',
    headers,
  });
  return handleResponse<void>(res);
}

// ─── Frames ───────────────────────────────────────────────────────────────────

export async function updateFrame(
  frameId: string,
  data: Partial<Pick<StoryboardFrame, 'note' | 'shot_type' | 'camera_angle' | 'camera_movement' | 'focal_length' | 'lighting' | 'duration_seconds' | 'transition_type' | 'annotations_json' | 'sort_order'>>
): Promise<StoryboardFrame> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/frames/${frameId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<StoryboardFrame>(res);
}

export async function reorderFrames(frameIds: string[]): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/frames/reorder`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ frame_ids: frameIds }),
  });
  return handleResponse<void>(res);
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
  const res = await fetch(`${getApiUrl()}/api/storyboard/ai/generate-image`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<{ task_id: string }>(res);
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
  const res = await fetch(`${getApiUrl()}/api/storyboard/ai/generate-video`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<{ task_id: string }>(res);
}

export interface SplitScriptParams {
  project_id: string;
  script_text: string;
  target_frames?: number;
  style?: string;
}

export async function splitScript(data: SplitScriptParams): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/ai/split-script`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  return handleResponse<{ task_id: string }>(res);
}

export async function chatWithAI(
  projectId: string,
  message: string,
  frameId?: string
): Promise<{ response: string; actions: unknown[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, frame_id: frameId }),
  });
  return handleResponse<{ response: string; actions: unknown[] }>(res);
}

// ─── Export ───────────────────────────────────────────────────────────────────

export type ExportFormat = 'pdf' | 'pptx' | 'video' | 'zip';

export async function exportProject(
  projectId: string,
  format: ExportFormat,
  options?: Record<string, unknown>
): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/storyboard/projects/${projectId}/export`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ format, options: options ?? {} }),
  });
  return handleResponse<{ task_id: string }>(res);
}
