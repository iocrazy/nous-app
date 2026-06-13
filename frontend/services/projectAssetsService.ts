// frontend/services/projectAssetsService.ts
// API client for Project Assets (IC-port P4).

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface ProjectAssetCanvas {
  canvas_id: string;
  canvas_name: string;
  kind: 'smart' | 'classic';
  asset_count: number;
}

export interface ProjectAssetTreeNode {
  project_id: string;
  name: string;
  canvases: ProjectAssetCanvas[];
}

export interface CanvasAssetItem {
  id: string;
  filename: string;
  file_type: string;
  mime_type: string | null;
  thumbnail_path: string | null;
  cover_image_path: string | null;
  created_at: string;
  role: 'reference' | 'output';
  node_id: string;
}

export interface CanvasBackRef {
  canvas_id: string;
  canvas_name: string;
  kind: 'smart' | 'classic';
  project_id: string;
  role: 'reference' | 'output';
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${getApiUrl()}/api/v1${path}`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  return json.data as T;
}

export function fetchProjectAssetsTree(): Promise<ProjectAssetTreeNode[]> {
  return getJson<ProjectAssetTreeNode[]>('/resources/project-assets/tree');
}

export function fetchCanvasAssets(canvasId: string): Promise<CanvasAssetItem[]> {
  return getJson<CanvasAssetItem[]>(`/canvases/${canvasId}/assets`);
}

export function fetchResourceCanvasRefs(resourceId: string): Promise<CanvasBackRef[]> {
  return getJson<CanvasBackRef[]>(`/resources/${resourceId}/canvas-refs`);
}
