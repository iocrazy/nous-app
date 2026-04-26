import { apiClient, apiFetch } from './apiClient';
import {
  StoryboardProject,
  StoryboardNode,
  StoryboardEdge,
  StoryboardFrame,
  StoryboardCharacter,
  ProjectSummary,
} from '../types';

interface Envelope<T> {
  success?: boolean;
  data: T;
}

// ─── Project CRUD ─────────────────────────────────────────────────────────────

export async function fetchProjects(
  teamId: string,
  page = 1,
  limit = 20,
  projectId?: string,
): Promise<{ data: ProjectSummary[]; total: number }> {
  const result = await apiClient.get<
    Envelope<{ items: ProjectSummary[]; total: number }>
  >('/api/v1/storyboard/projects', {
    query: { team_id: teamId, page, limit, project_id: projectId },
  });
  return {
    data: result.data?.items ?? [],
    total: result.data?.total ?? 0,
  };
}

export interface ProjectFull extends StoryboardProject {
  nodes: StoryboardNode[];
  edges: StoryboardEdge[];
  characters: StoryboardCharacter[];
}

export async function fetchProject(projectId: string): Promise<ProjectFull> {
  const result = await apiClient.get<
    Envelope<{
      project: StoryboardProject;
      nodes: StoryboardNode[];
      edges: StoryboardEdge[];
      characters: StoryboardCharacter[];
    }>
  >(`/api/v1/storyboard/projects/${projectId}`);
  return {
    ...result.data.project,
    nodes: result.data.nodes ?? [],
    edges: result.data.edges ?? [],
    characters: result.data.characters ?? [],
  };
}

export async function createProject(data: {
  team_id: string;
  name: string;
  description?: string;
  project_id?: string;
}): Promise<StoryboardProject> {
  const result = await apiClient.post<Envelope<StoryboardProject>>(
    '/api/v1/storyboard/projects',
    data,
  );
  return result.data;
}

export async function updateProject(
  projectId: string,
  data: Partial<
    Pick<
      StoryboardProject,
      'name' | 'description' | 'cover_image_url' | 'settings_json' | 'status'
    >
  >,
): Promise<StoryboardProject> {
  const result = await apiClient.patch<Envelope<StoryboardProject>>(
    `/api/v1/storyboard/projects/${projectId}`,
    data,
  );
  return result.data;
}

export async function deleteProject(projectId: string): Promise<void> {
  await apiClient.delete(`/api/v1/storyboard/projects/${projectId}`);
}

export async function updateViewport(
  projectId: string,
  viewport: { x: number; y: number; zoom: number },
): Promise<void> {
  await apiClient.patch(
    `/api/v1/storyboard/projects/${projectId}/viewport`,
    { viewport_json: viewport },
  );
}

// ─── Canvas Sync ──────────────────────────────────────────────────────────────

export interface CanvasSyncData {
  nodes: Array<
    Pick<
      StoryboardNode,
      | 'id'
      | 'position_x'
      | 'position_y'
      | 'width'
      | 'height'
      | 'data_json'
      | 'sort_order'
      | 'locked'
    >
  >;
  edges: Array<
    Pick<
      StoryboardEdge,
      | 'id'
      | 'source_node_id'
      | 'target_node_id'
      | 'source_handle'
      | 'target_handle'
      | 'edge_type'
    >
  >;
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
  syncData: CanvasSyncData,
): Promise<SyncResult> {
  const result = await apiClient.post<Envelope<SyncResult>>(
    `/api/v1/storyboard/projects/${projectId}/sync`,
    syncData,
  );
  return result.data;
}

// ─── Characters ───────────────────────────────────────────────────────────────

export async function fetchCharacters(
  projectId: string,
): Promise<StoryboardCharacter[]> {
  const result = await apiClient.get<Envelope<StoryboardCharacter[]>>(
    `/api/v1/storyboard/projects/${projectId}/characters`,
  );
  return result.data;
}

export async function createCharacter(
  projectId: string,
  data: { name: string; description?: string; visual_traits?: Record<string, string> },
): Promise<StoryboardCharacter> {
  const result = await apiClient.post<Envelope<StoryboardCharacter>>(
    `/api/v1/storyboard/projects/${projectId}/characters`,
    data,
  );
  return result.data;
}

export async function updateCharacter(
  characterId: string,
  data: Partial<
    Pick<
      StoryboardCharacter,
      | 'name'
      | 'description'
      | 'reference_image_url'
      | 'thumbnail_url'
      | 'visual_traits'
      | 'sort_order'
    >
  >,
): Promise<StoryboardCharacter> {
  const result = await apiClient.patch<Envelope<StoryboardCharacter>>(
    `/api/v1/storyboard/characters/${characterId}`,
    data,
  );
  return result.data;
}

export async function deleteCharacter(characterId: string): Promise<void> {
  await apiClient.delete(`/api/v1/storyboard/characters/${characterId}`);
}

// ─── Frames ───────────────────────────────────────────────────────────────────

export async function updateFrame(
  frameId: string,
  data: Partial<
    Pick<
      StoryboardFrame,
      | 'note'
      | 'shot_type'
      | 'camera_angle'
      | 'camera_movement'
      | 'focal_length'
      | 'lighting'
      | 'duration_seconds'
      | 'transition_type'
      | 'annotations_json'
      | 'sort_order'
    >
  >,
): Promise<StoryboardFrame> {
  const result = await apiClient.patch<Envelope<StoryboardFrame>>(
    `/api/v1/storyboard/frames/${frameId}`,
    data,
  );
  return result.data;
}

export async function reorderFrames(frameIds: string[]): Promise<void> {
  await apiClient.post('/api/v1/storyboard/frames/reorder', {
    frame_ids: frameIds,
  });
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

export async function generateImage(
  data: GenerateImageParams,
): Promise<{ task_id: string }> {
  // Endpoint returns { success, task_id } at the top level — no data wrapper.
  const body = await apiClient.post<{ success: boolean; task_id: string }>(
    '/api/v1/storyboard/generate/image',
    data,
  );
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

export async function generateVideo(
  data: GenerateVideoParams,
): Promise<{ task_id: string }> {
  const body = await apiClient.post<{ success: boolean; task_id: string }>(
    '/api/v1/storyboard/generate/video',
    data,
  );
  return { task_id: body.task_id };
}

export interface SplitScriptParams {
  project_id: string;
  script_text: string;
  target_frames?: number;
  style?: string;
}

export async function splitScript(
  data: SplitScriptParams,
): Promise<{ task_id: string }> {
  const body = await apiClient.post<{ success: boolean; task_id: string }>(
    '/api/v1/storyboard/split-script',
    data,
  );
  return { task_id: body.task_id };
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
  const formData = new FormData();
  formData.append('file', file);
  if (nodeId) formData.append('node_id', nodeId);

  const res = await apiFetch(
    `/api/v1/storyboard/projects/${projectId}/upload`,
    { method: 'POST', raw: formData },
  );
  const body: Envelope<UploadImageResult> = await res.json();
  return body.data;
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
  const result = await apiClient.post<Envelope<SplitImageResult>>(
    `/api/v1/storyboard/projects/${projectId}/split-image`,
    { asset_id: assetId, rows, cols, node_id: nodeId },
  );
  return result.data;
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
  options?: ExportProjectOptions,
): Promise<{ task_id: string }> {
  const result = await apiClient.post<Envelope<{ task_id: string }>>(
    `/api/v1/storyboard/projects/${projectId}/export`,
    { format, options: options ?? {} },
  );
  return result.data;
}
