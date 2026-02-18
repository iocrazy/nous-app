import { Project, ProjectFile, FileVersion, ReviewComment, ReviewStatus } from '../types';
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

  const response = await fetch(url, { headers: await getAuthHeaders() });
  if (!response.ok) throw new Error('Failed to fetch projects');
  const json = await response.json();
  return json.data || [];
};

export const createProject = async (data: { name: string; description?: string; team_id?: string; project_type?: string; project_group?: string; announcement?: string }): Promise<Project> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects`, {
    method: 'POST',
    headers: await getAuthHeaders(),
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
    headers: await getAuthHeaders(),
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
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete project');
};

export const fetchProjectFiles = async (projectId: string, includeTrashed?: boolean): Promise<ProjectFile[]> => {
  const apiUrl = getApiUrl();
  const qs = includeTrashed ? '?include_trashed=true' : '';
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files${qs}`, {
    headers: await getAuthHeaders(),
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
  const authHeaders = await getAuthHeaders();
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

export const linkVideoToProject = async (projectId: string, mediaId: string): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/link-video`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ media_id: mediaId }),
  });
  if (!response.ok) throw new Error('Failed to link video');
  const json = await response.json();
  return json.data;
};

export const getFileInfo = async (projectId: string, fileId: string): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to get file info');
  const json = await response.json();
  return json.data;
};

export const updateFile = async (projectId: string, fileId: string, data: Partial<ProjectFile>): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
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
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete file');
};

// ============================================
// File versions
// ============================================

export const fetchFileVersions = async (projectId: string, fileId: string): Promise<FileVersion[]> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}/versions`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch versions');
  const json = await response.json();
  return json.data || [];
};

export const uploadNewVersion = async (projectId: string, fileId: string, file: File, notes?: string): Promise<FileVersion> => {
  const apiUrl = getApiUrl();
  const formData = new FormData();
  formData.append('file', file);

  const headers: Record<string, string> = {};
  const authHeaders = await getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const qs = notes ? `?notes=${encodeURIComponent(notes)}` : '';
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}/versions${qs}`, {
    method: 'POST',
    headers,
    body: formData,
  });
  if (!response.ok) throw new Error('Failed to upload version');
  const json = await response.json();
  return json.data;
};

// ============================================
// Review comments
// ============================================

export const fetchComments = async (projectId: string, fileId: string, versionId?: string): Promise<ReviewComment[]> => {
  const apiUrl = getApiUrl();
  const qs = versionId ? `?version_id=${versionId}` : '';
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}/comments${qs}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch comments');
  const json = await response.json();
  return json.data || [];
};

export const addComment = async (
  projectId: string,
  fileId: string,
  data: { content: string; timestamp_seconds?: number | null; version_id?: string | null; drawing_data?: any | null }
): Promise<ReviewComment> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}/comments`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to add comment');
  const json = await response.json();
  return json.data;
};

export const deleteComment = async (projectId: string, fileId: string, commentId: string): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}/comments/${commentId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete comment');
};

// ============================================
// Review status
// ============================================

export const updateReviewStatus = async (projectId: string, fileId: string, status: ReviewStatus | null): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}/review-status`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ review_status: status }),
  });
  if (!response.ok) throw new Error('Failed to update review status');
  const json = await response.json();
  return json.data;
};
