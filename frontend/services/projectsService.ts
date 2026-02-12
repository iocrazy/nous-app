import { Project, ProjectFile } from '../types';
import { getAuthHeaders } from './parserService';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

export const fetchProjects = async (params?: { type?: string; starred?: boolean }): Promise<Project[]> => {
  const apiUrl = getApiUrl();
  const searchParams = new URLSearchParams();
  if (params?.type) searchParams.set('project_type', params.type);
  if (params?.starred !== undefined) searchParams.set('starred', String(params.starred));
  const qs = searchParams.toString();
  const url = `${apiUrl}/api/v1/projects${qs ? '?' + qs : ''}`;

  const response = await fetch(url, { headers: getAuthHeaders() });
  if (!response.ok) throw new Error('Failed to fetch projects');
  const json = await response.json();
  return json.data || [];
};

export const createProject = async (data: { name: string; description?: string; team_id?: string; project_type?: string; project_group?: string }): Promise<Project> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create project');
  }
  const json = await response.json();
  return json.data;
};

export const updateProject = async (id: string, data: Partial<Project>): Promise<Project> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${id}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update project');
  const json = await response.json();
  return json.data;
};

export const deleteProject = async (id: string): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${id}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete project');
};

export const fetchProjectFiles = async (projectId: string, includeTrashed?: boolean): Promise<ProjectFile[]> => {
  const apiUrl = getApiUrl();
  const qs = includeTrashed ? '?include_trashed=true' : '';
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files${qs}`, {
    headers: getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch files');
  const json = await response.json();
  return json.data || [];
};

export const uploadFile = async (projectId: string, file: File, notes?: string): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const formData = new FormData();
  formData.append('file', file);

  // Build headers without Content-Type (let browser set multipart boundary)
  const headers: Record<string, string> = {};
  const authHeaders = getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const qs = notes ? `?notes=${encodeURIComponent(notes)}` : '';
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/upload${qs}`, {
    method: 'POST',
    headers,
    body: formData,
  });
  if (!response.ok) throw new Error('Failed to upload file');
  const json = await response.json();
  return json.data;
};

export const linkVideoToProject = async (projectId: string, videoId: string): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/link-video`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ video_id: videoId }),
  });
  if (!response.ok) throw new Error('Failed to link video');
  const json = await response.json();
  return json.data;
};

export const getFileInfo = async (projectId: string, fileId: string): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}`, {
    headers: getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to get file info');
  const json = await response.json();
  return json.data;
};

export const updateFile = async (projectId: string, fileId: string, data: Partial<ProjectFile>): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update file');
  const json = await response.json();
  return json.data;
};

export const deleteFile = async (projectId: string, fileId: string): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete file');
};
