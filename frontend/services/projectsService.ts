import {
  Project,
  ProjectFile,
  ProjectFolder,
  ProjectMember,
  ProjectShare,
  FileVersion,
  ReviewComment,
  ReviewStatus,
  ProjectStage,
} from '../types';
import { apiClient, apiFetch } from './apiClient';

interface Envelope<T> {
  data?: T;
}

// ============================================
// Projects
// ============================================

export const fetchProjects = async (params?: {
  type?: string;
  starred?: boolean;
  teamId?: string;
}): Promise<Project[]> => {
  const response = await apiClient.get<Envelope<Project[]>>('/api/v1/projects', {
    query: {
      project_type: params?.type,
      starred: params?.starred,
      team_id: params?.teamId,
    },
  });
  return response.data || [];
};

export const createProject = async (data: {
  name: string;
  description?: string;
  team_id?: string;
  project_type?: string;
  project_group?: string;
  announcement?: string;
}): Promise<Project> => {
  const response = await apiClient.post<Envelope<Project>>(
    '/api/v1/projects',
    data,
  );
  if (!response.data) throw new Error('Empty response from createProject');
  return response.data;
};

export const updateProject = async (
  id: string,
  data: Partial<Project>,
): Promise<Project> => {
  const response = await apiClient.put<Envelope<Project>>(
    `/api/v1/projects/${id}`,
    data,
  );
  if (!response.data) throw new Error('Empty response from updateProject');
  return response.data;
};

export const deleteProject = async (id: string): Promise<void> => {
  await apiClient.delete(`/api/v1/projects/${id}`);
};

// ============================================
// Project files
// ============================================

export const fetchProjectFiles = async (
  projectId: string,
  includeTrashed?: boolean,
  folderId?: string | null,
): Promise<ProjectFile[]> => {
  const response = await apiClient.get<Envelope<ProjectFile[]>>(
    `/api/v1/projects/${projectId}/files`,
    {
      query: {
        include_trashed: includeTrashed ? 'true' : undefined,
        folder_id: folderId ?? undefined,
      },
    },
  );
  return response.data || [];
};

export const uploadFile = async (
  projectId: string,
  file: File,
  notes?: string,
): Promise<ProjectFile> => {
  // FormData upload: use apiFetch directly so we can pass `raw` body.
  const formData = new FormData();
  formData.append('file', file);

  const response = await apiFetch(
    `/api/v1/projects/${projectId}/files/upload`,
    {
      method: 'POST',
      raw: formData,
      query: { notes: notes ?? undefined },
    },
  );
  const json: Envelope<ProjectFile> = await response.json();
  if (!json.data) throw new Error('Empty response from uploadFile');
  return json.data;
};

export const linkVideoToProject = async (
  projectId: string,
  mediaId: string,
): Promise<ProjectFile> => {
  const response = await apiClient.post<Envelope<ProjectFile>>(
    `/api/v1/projects/${projectId}/files/link-media`,
    { media_id: mediaId },
  );
  if (!response.data) throw new Error('Empty response from linkVideoToProject');
  return response.data;
};

export const getFileInfo = async (
  projectId: string,
  fileId: string,
): Promise<ProjectFile> => {
  const response = await apiClient.get<Envelope<ProjectFile>>(
    `/api/v1/projects/${projectId}/files/${fileId}`,
  );
  if (!response.data) throw new Error('Empty response from getFileInfo');
  return response.data;
};

export const updateFile = async (
  projectId: string,
  fileId: string,
  data: Partial<ProjectFile>,
): Promise<ProjectFile> => {
  const response = await apiClient.put<Envelope<ProjectFile>>(
    `/api/v1/projects/${projectId}/files/${fileId}`,
    data,
  );
  if (!response.data) throw new Error('Empty response from updateFile');
  return response.data;
};

export const deleteFile = async (
  projectId: string,
  fileId: string,
): Promise<void> => {
  await apiClient.delete(`/api/v1/projects/${projectId}/files/${fileId}`);
};

// ============================================
// File versions
// ============================================

export const fetchFileVersions = async (
  projectId: string,
  fileId: string,
): Promise<FileVersion[]> => {
  const response = await apiClient.get<Envelope<FileVersion[]>>(
    `/api/v1/projects/${projectId}/files/${fileId}/versions`,
  );
  return response.data || [];
};

export const uploadNewVersion = async (
  projectId: string,
  fileId: string,
  file: File,
  notes?: string,
): Promise<FileVersion> => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await apiFetch(
    `/api/v1/projects/${projectId}/files/${fileId}/versions`,
    {
      method: 'POST',
      raw: formData,
      query: { notes: notes ?? undefined },
    },
  );
  const json: Envelope<FileVersion> = await response.json();
  if (!json.data) throw new Error('Empty response from uploadNewVersion');
  return json.data;
};

// ============================================
// Review comments
// ============================================

export const fetchComments = async (
  projectId: string,
  fileId: string,
  versionId?: string,
): Promise<ReviewComment[]> => {
  const response = await apiClient.get<Envelope<ReviewComment[]>>(
    `/api/v1/projects/${projectId}/files/${fileId}/comments`,
    { query: { version_id: versionId } },
  );
  return response.data || [];
};

export const addComment = async (
  projectId: string,
  fileId: string,
  data: {
    content: string;
    timestamp_seconds?: number | null;
    version_id?: string | null;
    drawing_data?: any | null;
  },
): Promise<ReviewComment> => {
  const response = await apiClient.post<Envelope<ReviewComment>>(
    `/api/v1/projects/${projectId}/files/${fileId}/comments`,
    data,
  );
  if (!response.data) throw new Error('Empty response from addComment');
  return response.data;
};

export const deleteComment = async (
  projectId: string,
  fileId: string,
  commentId: string,
): Promise<void> => {
  await apiClient.delete(
    `/api/v1/projects/${projectId}/files/${fileId}/comments/${commentId}`,
  );
};

// ============================================
// Review status
// ============================================

export const updateReviewStatus = async (
  projectId: string,
  fileId: string,
  status: ReviewStatus | null,
): Promise<ProjectFile> => {
  const response = await apiClient.put<Envelope<ProjectFile>>(
    `/api/v1/projects/${projectId}/files/${fileId}/review-status`,
    { review_status: status },
  );
  if (!response.data) throw new Error('Empty response from updateReviewStatus');
  return response.data;
};

// ============================================
// Project folders
// ============================================

export const fetchProjectFolders = async (
  projectId: string,
  parentId?: string | null,
): Promise<ProjectFolder[]> => {
  const response = await apiClient.get<Envelope<ProjectFolder[]>>(
    `/api/v1/projects/${projectId}/folders`,
    { query: { parent_id: parentId ?? undefined } },
  );
  return response.data || [];
};

export const createProjectFolder = async (
  projectId: string,
  name: string,
  parentId?: string | null,
): Promise<ProjectFolder> => {
  const body: Record<string, string> = { name };
  if (parentId) body.parent_id = parentId;
  const response = await apiClient.post<Envelope<ProjectFolder>>(
    `/api/v1/projects/${projectId}/folders`,
    body,
  );
  if (!response.data) throw new Error('Empty response from createProjectFolder');
  return response.data;
};

export const renameProjectFolder = async (
  projectId: string,
  folderId: string,
  name: string,
): Promise<ProjectFolder> => {
  const response = await apiClient.put<Envelope<ProjectFolder>>(
    `/api/v1/projects/${projectId}/folders/${folderId}`,
    { name },
  );
  if (!response.data) throw new Error('Empty response from renameProjectFolder');
  return response.data;
};

export const deleteProjectFolder = async (
  projectId: string,
  folderId: string,
): Promise<void> => {
  await apiClient.delete(`/api/v1/projects/${projectId}/folders/${folderId}`);
};

export const moveFileToFolder = async (
  projectId: string,
  fileId: string,
  folderId: string | null,
): Promise<ProjectFile> => {
  const response = await apiClient.put<Envelope<ProjectFile>>(
    `/api/v1/projects/${projectId}/files/${fileId}/move`,
    { folder_id: folderId },
  );
  if (!response.data) throw new Error('Empty response from moveFileToFolder');
  return response.data;
};

// ============================================
// Project members
// ============================================

export const fetchProjectMembers = async (
  projectId: string,
): Promise<ProjectMember[]> => {
  const response = await apiClient.get<Envelope<ProjectMember[]>>(
    `/api/v1/projects/${projectId}/members`,
  );
  return response.data || [];
};

export const addProjectMember = async (
  projectId: string,
  userId: string,
  role: string = 'viewer',
): Promise<ProjectMember> => {
  const response = await apiClient.post<Envelope<ProjectMember>>(
    `/api/v1/projects/${projectId}/members`,
    { user_id: userId, role },
  );
  if (!response.data) throw new Error('Empty response from addProjectMember');
  return response.data;
};

export const updateMemberRole = async (
  projectId: string,
  memberId: string,
  role: string,
): Promise<ProjectMember> => {
  const response = await apiClient.put<Envelope<ProjectMember>>(
    `/api/v1/projects/${projectId}/members/${memberId}`,
    { role },
  );
  if (!response.data) throw new Error('Empty response from updateMemberRole');
  return response.data;
};

export const removeProjectMember = async (
  projectId: string,
  memberId: string,
): Promise<void> => {
  await apiClient.delete(`/api/v1/projects/${projectId}/members/${memberId}`);
};

// ============================================
// Project shares
// ============================================

export const fetchProjectShares = async (
  projectId: string,
): Promise<ProjectShare[]> => {
  const response = await apiClient.get<Envelope<ProjectShare[]>>(
    `/api/v1/projects/${projectId}/shares`,
  );
  return response.data || [];
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
  const response = await apiClient.post<Envelope<any>>(
    `/api/v1/projects/${projectId}/shares`,
    data,
  );
  return response.data;
};

// ============================================
// Project collections
// ============================================

export const fetchProjectCollections = async (
  projectId: string,
): Promise<any[]> => {
  const response = await apiClient.get<Envelope<any[]>>(
    `/api/v1/projects/${projectId}/collections`,
  );
  return response.data || [];
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
  const response = await apiClient.post<Envelope<any>>(
    `/api/v1/projects/${projectId}/collections`,
    data,
  );
  return response.data;
};

export const deleteProjectCollection = async (
  projectId: string,
  collectionId: string,
): Promise<void> => {
  await apiClient.delete(
    `/api/v1/projects/${projectId}/collections/${collectionId}`,
  );
};

// ============================================
// SOP Stage endpoints (Phase 5b)
// ============================================

/** Fetch the global SOP stage catalog (ordered by sort_order). */
export const fetchStageCatalog = async (): Promise<ProjectStage[]> => {
  const response = await apiClient.get<Envelope<ProjectStage[]>>(
    '/api/v1/projects/stages/catalog',
  );
  return response.data ?? [];
};

/** Fetch the current SOP stage for a project, or null when unset. */
export const fetchCurrentStage = async (
  projectId: string,
): Promise<ProjectStage | null> => {
  const response = await apiClient.get<Envelope<ProjectStage | null>>(
    `/api/v1/projects/${projectId}/current_stage`,
  );
  return response.data ?? null;
};

/**
 * Set the project's current SOP stage. Returns the new stage row, or null
 * when the project was already on the requested stage (server-side no-op).
 */
export const setCurrentStage = async (
  projectId: string,
  stageId: string,
): Promise<ProjectStage | null> => {
  const response = await apiClient.put<Envelope<ProjectStage | null>>(
    `/api/v1/projects/${projectId}/current_stage`,
    { stage_id: Number(stageId) },
  );
  return response.data ?? null;
};
