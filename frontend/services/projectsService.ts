import { Project, ProjectFile, ProjectFolder, ProjectMember, ProjectShare, FileVersion, ReviewComment, ReviewStatus } from '../types';
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export const fetchProjects = async (params?: { type?: string; starred?: boolean; teamId?: string }): Promise<Project[]> => {
  const apiUrl = getApiUrl();
  const searchParams = new URLSearchParams();
  if (params?.type) searchParams.set('project_type', params.type);
  if (params?.starred !== undefined) searchParams.set('starred', String(params.starred));
  if (params?.teamId !== undefined) searchParams.set('team_id', params.teamId);
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

export const fetchProjectFiles = async (
  projectId: string,
  includeTrashed?: boolean,
  folderId?: string | null,
): Promise<ProjectFile[]> => {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams();
  if (includeTrashed) params.set('include_trashed', 'true');
  if (folderId) params.set('folder_id', folderId);
  const qs = params.toString();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files${qs ? '?' + qs : ''}`, {
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
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/link-media`, {
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

// ============================================
// Project folders
// ============================================

export const fetchProjectFolders = async (
  projectId: string,
  parentId?: string | null,
): Promise<ProjectFolder[]> => {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams();
  if (parentId) params.set('parent_id', parentId);
  const qs = params.toString();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/folders${qs ? '?' + qs : ''}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch folders');
  const json = await response.json();
  return json.data || [];
};

export const createProjectFolder = async (
  projectId: string,
  name: string,
  parentId?: string | null,
): Promise<ProjectFolder> => {
  const apiUrl = getApiUrl();
  const body: Record<string, string> = { name };
  if (parentId) body.parent_id = parentId;
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/folders`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error('Failed to create folder');
  const json = await response.json();
  return json.data;
};

export const renameProjectFolder = async (
  projectId: string,
  folderId: string,
  name: string,
): Promise<ProjectFolder> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/folders/${folderId}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw new Error('Failed to rename folder');
  const json = await response.json();
  return json.data;
};

export const deleteProjectFolder = async (
  projectId: string,
  folderId: string,
): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/folders/${folderId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete folder');
};

export const moveFileToFolder = async (
  projectId: string,
  fileId: string,
  folderId: string | null,
): Promise<ProjectFile> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/files/${fileId}/move`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ folder_id: folderId }),
  });
  if (!response.ok) throw new Error('Failed to move file');
  const json = await response.json();
  return json.data;
};

// ============================================
// Project members
// ============================================

export const fetchProjectMembers = async (projectId: string): Promise<ProjectMember[]> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/members`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch members');
  const json = await response.json();
  return json.data || [];
};

export const addProjectMember = async (
  projectId: string,
  userId: string,
  role: string = 'viewer',
): Promise<ProjectMember> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/members`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ user_id: userId, role }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to add member');
  }
  const json = await response.json();
  return json.data;
};

export const updateMemberRole = async (
  projectId: string,
  memberId: string,
  role: string,
): Promise<ProjectMember> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/members/${memberId}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ role }),
  });
  if (!response.ok) throw new Error('Failed to update member role');
  const json = await response.json();
  return json.data;
};

export const removeProjectMember = async (
  projectId: string,
  memberId: string,
): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/members/${memberId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to remove member');
};

// ============================================
// Project shares
// ============================================

export const fetchProjectShares = async (projectId: string): Promise<ProjectShare[]> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/shares`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch shares');
  const json = await response.json();
  return json.data || [];
};

export const createProjectShare = async (
  projectId: string,
  data: {
    file_id: string;
    share_type?: string;
    share_name?: string;
    password?: string;
    allow_download?: boolean;
    expires_hours?: number;
  },
): Promise<any> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/shares`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to create share');
  const json = await response.json();
  return json.data;
};

// ============================================
// Project collections
// ============================================

export const fetchProjectCollections = async (projectId: string): Promise<any[]> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/collections`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch collections');
  const json = await response.json();
  return json.data || [];
};

export const createProjectCollection = async (
  projectId: string,
  data: {
    collection_name: string;
    allowed_types?: string[];
    max_file_size_mb?: number;
    deadline?: string;
  },
): Promise<any> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/collections`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to create collection');
  const json = await response.json();
  return json.data;
};

export const deleteProjectCollection = async (
  projectId: string,
  collectionId: string,
): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/collections/${collectionId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete collection');
};
