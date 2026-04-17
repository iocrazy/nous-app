import { supabase } from '../supabaseClient';
import { Folder, Resource, ResourceItem, ResourceVersion, SmartCollection } from '../types';
import { getAuthHeaders } from './parserService';
import { apiClient } from './apiClient';
import { getApiUrl } from '../utils/apiConfig';
import { buildMediaUrl } from '../utils/mediaUrl';

// ─── Folders ────────────────────────────────────────────

export async function fetchFolders(
  scopeType: 'personal' | 'team',
  scopeId: string,
  libraryId?: string | null
): Promise<Folder[]> {
  let query = supabase
    .from('folders')
    .select('*')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('is_trashed', false);

  if (libraryId) {
    query = query.eq('library_id', libraryId);
  } else if (scopeType === 'team') {
    // Team mode without library: show nothing (libraries are the entry point)
    query = query.is('library_id', null);
  }

  const { data, error } = await query.order('sort_order', { ascending: true });

  if (error) throw error;
  return data || [];
}

export async function createFolder(folder: {
  name: string;
  parent_id?: string | null;
  scope_type: 'personal' | 'team';
  scope_id: string;
}): Promise<Folder> {
  const user = (await supabase.auth.getUser()).data.user;
  if (!user) throw new Error('Not authenticated');

  const { data, error } = await supabase
    .from('folders')
    .insert({ ...folder, created_by: user.id })
    .select()
    .single();

  if (error) throw error;
  return data;
}

export async function renameFolder(id: string, name: string): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({ name })
    .eq('id', id);

  if (error) throw error;
}

export async function renameResource(resourceId: string, filename: string): Promise<void> {
  const { error } = await supabase
    .from('resources')
    .update({ filename })
    .eq('id', resourceId);

  if (error) throw error;
}

export async function updateResource(
  resourceId: string,
  data: { filename?: string; notes?: string; url?: string; rating?: number },
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}`, {
    method: 'PATCH',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update resource');
  const json = await response.json();
  return json.data;
}

export async function trashFolder(id: string): Promise<void> {
  await apiClient.post(`/api/v1/resources/folders/${id}/trash`);
}

export async function getFolderContentCount(
  id: string,
): Promise<{ resource_count: number; subfolder_count: number }> {
  const json = await apiClient.get<{
    data: { resource_count: number; subfolder_count: number };
  }>(`/api/v1/resources/folders/${id}/content-count`);
  return json.data;
}

// Fetch resource_items inside a specific folder (used for trashed folder contents in recycle bin)
export async function fetchFolderContents(
  folderId: string,
  includeTrashed = false,
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)')
    .eq('folder_id', folderId);
  if (!includeTrashed) {
    query = query.eq('resource.is_trashed', false);
  }
  const { data, error } = await query.order('created_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

export async function fetchTrashedFolders(
  scopeType: 'personal' | 'team',
  scopeId: string,
): Promise<Folder[]> {
  const { data, error } = await supabase
    .from('folders')
    .select('*')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('is_trashed', true)
    .order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
}

export async function restoreFolder(id: string): Promise<void> {
  await apiClient.post(`/api/v1/resources/folders/${id}/restore`);
}

// Build folder tree from flat list
export function buildFolderTree(folders: Folder[]): Folder[] {
  const map = new Map<string, Folder>();
  const roots: Folder[] = [];

  folders.forEach(f => map.set(f.id, { ...f, children: [] }));

  map.forEach(folder => {
    if (folder.parent_id && map.has(folder.parent_id)) {
      map.get(folder.parent_id)!.children!.push(folder);
    } else {
      roots.push(folder);
    }
  });

  return roots;
}

// ─── Child Folders (direct children of a parent) ────────

export async function fetchChildFolders(
  scopeType: 'personal' | 'team',
  scopeId: string,
  parentId: string | null,
  libraryId?: string | null
): Promise<Folder[]> {
  let query = supabase
    .from('folders')
    .select('*')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('is_trashed', false);

  if (parentId) {
    query = query.eq('parent_id', parentId);
  } else {
    query = query.is('parent_id', null);
  }

  if (libraryId) {
    query = query.eq('library_id', libraryId);
  } else if (scopeType === 'team') {
    query = query.is('library_id', null);
  }

  const { data, error } = await query.order('sort_order', { ascending: true });
  if (error) throw error;
  return data || [];
}

// ─── Resource Context (for detail page sibling files) ───

export async function fetchResourceContext(
  resourceId: string
): Promise<{ folder_id: string | null; scope_type: string; scope_id: string; library_id: string | null } | null> {
  const { data, error } = await supabase
    .from('resource_items')
    .select('folder_id, scope_type, scope_id, library_id')
    .eq('resource_id', resourceId)
    .limit(1)
    .single();

  if (error) return null;
  return data;
}

// ─── Resources ──────────────────────────────────────────

export async function fetchResources(
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
  libraryId?: string | null
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('resources.is_trashed', false)
    .neq('resource.source_type', 'web');

  if (folderId) {
    query = query.eq('folder_id', folderId);
  } else {
    query = query.is('folder_id', null);
  }

  if (libraryId) {
    query = query.eq('library_id', libraryId);
  } else if (scopeType === 'team') {
    query = query.is('library_id', null);
  }

  const { data, error } = await query.order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
}

export async function fetchResourceCount(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<number> {
  const { count, error } = await supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)', { count: 'exact', head: true })
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('resources.is_trashed', false)
    .neq('resource.source_type', 'web');

  if (error) throw error;
  return count || 0;
}

// ─── Duplicate Detection ─────────────────────────────────

export async function checkDuplicate(
  fileHash: string,
  fileSize: number,
): Promise<{ duplicate: boolean; existing: Resource | null }> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    file_hash: fileHash,
    file_size: String(fileSize),
  });
  try {
    const response = await fetch(`${apiUrl}/api/v1/resources/check-duplicate?${params}`, {
      headers: await getAuthHeaders(),
    });
    if (!response.ok) return { duplicate: false, existing: null };
    return response.json();
  } catch {
    return { duplicate: false, existing: null };
  }
}

export async function linkExistingResource(
  resourceId: string,
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
  libraryId?: string | null,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    resource_id: resourceId,
    scope_type: scopeType,
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);
  if (libraryId) params.set('library_id', libraryId);

  const response = await fetch(`${apiUrl}/api/v1/resources/link-existing?${params}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to link resource');
  const json = await response.json();
  return json.data;
}

// ─── Upload ──────────────────────────────────────────────

export async function uploadResource(
  file: File,
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
  onProgress?: (progress: number) => void,
  libraryId?: string | null,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const formData = new FormData();
  formData.append('file', file);

  // Build headers without Content-Type (browser sets multipart boundary)
  const headers: Record<string, string> = {};
  const authHeaders = await getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const params = new URLSearchParams({
    scope_type: scopeType,
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);
  if (libraryId) params.set('library_id', libraryId);

  // Use XMLHttpRequest for progress tracking
  if (onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const json = JSON.parse(xhr.responseText);
          resolve(json.data);
        } else {
          reject(new Error('Failed to upload resource'));
        }
      });
      xhr.addEventListener('error', () => reject(new Error('Upload failed')));
      xhr.open('POST', `${apiUrl}/api/v1/resources/upload?${params}`);
      Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
      xhr.send(formData);
    });
  }

  const response = await fetch(`${apiUrl}/api/v1/resources/upload?${params}`, {
    method: 'POST',
    headers,
    body: formData,
  });
  if (!response.ok) throw new Error('Failed to upload resource');
  const json = await response.json();
  return json.data;
}

// ─── Trash / Restore ─────────────────────────────────────

export async function trashResource(
  resourceId: string,
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    scope_type: scopeType,
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);

  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}?${params}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to trash resource');
}

export async function restoreResource(resourceId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/restore`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to restore resource');
}

export async function permanentDeleteResource(resourceId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/permanent`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to permanently delete resource');
}

export async function permanentDeleteFolder(folderId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/folders/${folderId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to permanently delete folder');
}

// ─── Trashed resources ───────────────────────────────────

export async function fetchTrashedResources(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<ResourceItem[]> {
  // Query resources where is_trashed=true.
  // For personal scope: filter by creator_id (most reliable, works even if last_scope is null).
  // For team scope: filter by last_scope fields.
  let query = supabase
    .from('resources')
    .select('*')
    .eq('is_trashed', true);

  if (scopeType === 'personal') {
    query = query.eq('creator_id', scopeId);
  } else {
    query = query.eq('last_scope_type', scopeType).eq('last_scope_id', scopeId);
  }

  const { data, error } = await query.order('trashed_at', { ascending: false });

  if (error) throw error;

  // Wrap each resource in a ResourceItem-like shape for compatibility
  return (data || []).map((resource): ResourceItem => ({
    id: resource.id,
    resource_id: resource.id,
    scope_type: scopeType,
    scope_id: scopeId,
    folder_id: resource.last_folder_id ?? null,
    library_id: resource.last_library_id ?? null,
    added_by: resource.created_by ?? null,
    created_at: resource.created_at,
    resource,
  }));
}

// ─── Single Resource ────────────────────────────────────

export async function fetchResourceById(resourceId: string): Promise<Resource> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch resource');
  const json = await response.json();
  return json.data;
}

// ─── Versions ───────────────────────────────────────────

export async function fetchResourceVersions(resourceId: string): Promise<ResourceVersion[]> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/versions`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch versions');
  const json = await response.json();
  return json.data || [];
}

// ─── Version Management ─────────────────────────────────

export async function uploadNewVersion(
  resourceId: string,
  file: File,
  notes?: string,
): Promise<ResourceVersion> {
  const apiUrl = getApiUrl();
  const formData = new FormData();
  formData.append('file', file);

  const headers: Record<string, string> = {};
  const authHeaders = await getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const params = new URLSearchParams();
  if (notes) params.set('notes', notes);

  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions?${params}`,
    { method: 'POST', headers, body: formData },
  );
  if (!response.ok) throw new Error('Failed to upload new version');
  const json = await response.json();
  return json.data;
}

export async function setCurrentVersion(
  resourceId: string,
  versionNumber: number,
): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionNumber}/set-current`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!response.ok) throw new Error('Failed to set current version');
}

export async function deleteVersion(
  resourceId: string,
  versionId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}`,
    { method: 'DELETE', headers: await getAuthHeaders() },
  );
  if (!response.ok) throw new Error('Failed to delete version');
}

// ─── File URL ───────────────────────────────────────────

/**
 * Build a /media/{id} URL for a resource.
 * Backend resolves the file path from DB — the URL never exposes filenames.
 */
export function getResourceMediaUrl(resourceId: string, token?: string): string {
  return buildMediaUrl(resourceId, token);
}

export function getResourceFileUrl(resourceId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/file`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export function getResourceCoverUrl(resourceId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/cover`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export function getVersionFileUrl(resourceId: string, versionId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/file`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export function getVersionHlsUrl(resourceId: string, versionId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/hls/master.m3u8`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export async function retryTranscode(resourceId: string, versionId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/transcode`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to queue transcode');
  }
}

export function getPreviewSpriteUrl(resourceId: string): string {
  const apiUrl = getApiUrl();
  return `${apiUrl}/api/v1/resources/${resourceId}/preview-sprite`;
}

// ─── Tags ────────────────────────────────────────────────

export async function fetchResourceTags(resourceId: string) {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch resource tags');
  const json = await response.json();
  return json.data || [];
}

export async function addResourceTag(resourceId: string, tagId: string) {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ tag_id: tagId }),
  });
  if (!response.ok) throw new Error('Failed to add tag');
  const json = await response.json();
  return json.data;
}

export async function removeResourceTag(resourceId: string, tagId: string) {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags/${tagId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to remove tag');
}

// ─── Downloaded Resources (from Parser) ─────────────

export async function fetchDownloadedResources(
  scopeType: 'personal' | 'team',
  scopeId: string,
): Promise<ResourceItem[]> {
  const { data, error } = await supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('resource.source_type', 'web')
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

// ─── Smart Folders ───────────────────────────────────────

export interface SmartFolderCondition {
  field: string;
  op: string;
  value: string;
}

export interface SmartFolderRules {
  operator: 'AND' | 'OR';
  match: boolean;
  conditions: SmartFolderCondition[];
}

export async function fetchSmartFolders(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<SmartCollection[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_type: scopeType, scope_id: scopeId });
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch smart folders');
  const json = await response.json();
  return json.data || [];
}

export async function createSmartFolder(
  name: string,
  scopeType: 'personal' | 'team',
  scopeId: string,
  rules: SmartFolderRules,
): Promise<SmartCollection> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ name, scope_type: scopeType, scope_id: scopeId, rules }),
  });
  if (!response.ok) throw new Error('Failed to create smart folder');
  const json = await response.json();
  return json.data;
}

export async function updateSmartFolder(
  folderId: string,
  update: { name?: string; rules?: SmartFolderRules },
): Promise<SmartCollection> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders/${folderId}`, {
    method: 'PATCH',
    headers: await getAuthHeaders(),
    body: JSON.stringify(update),
  });
  if (!response.ok) throw new Error('Failed to update smart folder');
  const json = await response.json();
  return json.data;
}

export async function deleteSmartFolder(folderId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders/${folderId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete smart folder');
}

export async function fetchSmartFolderResults(
  folderId: string,
  scopeType: 'personal' | 'team',
  scopeId: string,
): Promise<ResourceItem[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_type: scopeType, scope_id: scopeId });
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders/${folderId}/results?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch smart folder results');
  const json = await response.json();
  return json.data || [];
}

/**
 * Query resources matching a smart folder's rules (client-side fallback).
 */
export async function fetchSmartFolderResources(
  scopeType: 'personal' | 'team',
  scopeId: string,
  rules: { match: string; conditions: Array<{ field: string; operator: string; value: string }> }
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('resources.is_trashed', false);

  for (const cond of (rules.conditions || [])) {
    const col = `resource.${cond.field}`;
    switch (cond.operator) {
      case 'equals':
        query = query.eq(col, cond.value);
        break;
      case 'contains':
        query = query.ilike(col, `%${cond.value}%`);
        break;
      case 'starts_with':
        query = query.ilike(col, `${cond.value}%`);
        break;
      case 'greater_than':
        query = query.gt(col, cond.value);
        break;
      case 'less_than':
        query = query.lt(col, cond.value);
        break;
    }
  }

  const { data, error } = await query.order('created_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

// ─── Move / Copy / Batch ─────────────────────────────

// 移动文件到指定文件夹
export async function moveResourceItem(
  resourceItemId: string,
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const update: Record<string, any> = { folder_id: targetFolderId };
  if (targetLibraryId !== undefined) update.library_id = targetLibraryId;
  const { data, error } = await supabase
    .from('resource_items')
    .update(update)
    .eq('id', resourceItemId)
    .select('id');
  if (error) throw error;
  if (!data || data.length === 0) {
    throw new Error('Move failed: item not found or permission denied');
  }
}

// 批量移动
export async function moveResourceItems(
  resourceItemIds: string[],
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const update: Record<string, any> = { folder_id: targetFolderId };
  if (targetLibraryId !== undefined) update.library_id = targetLibraryId;
  const { data, error } = await supabase
    .from('resource_items')
    .update(update)
    .in('id', resourceItemIds)
    .select('id');
  if (error) throw error;
  if (!data || data.length === 0) {
    throw new Error('Move failed: items not found or permission denied');
  }
}

// 复制文件（创建新 resource_item 指向同一个 resource）
export async function copyResourceItem(
  resourceId: string,
  targetScopeType: 'personal' | 'team',
  targetScopeId: string,
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<ResourceItem> {
  const user = (await supabase.auth.getUser()).data.user;
  if (!user) throw new Error('Not authenticated');
  const { data, error } = await supabase
    .from('resource_items')
    .insert({
      resource_id: resourceId,
      scope_type: targetScopeType,
      scope_id: targetScopeId,
      folder_id: targetFolderId,
      library_id: targetLibraryId || null,
      added_by: user.id,
    })
    .select('*, resource:resources(*)')
    .single();
  if (error) throw error;
  return data;
}

// 移动文件夹（级联更新子文件夹和 resource_items 的 library_id）
export async function moveFolder(
  folderId: string,
  targetParentId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const update: Record<string, any> = { parent_id: targetParentId };
  if (targetLibraryId !== undefined) update.library_id = targetLibraryId;
  const { error } = await supabase
    .from('folders')
    .update(update)
    .eq('id', folderId);
  if (error) throw error;

  // Cascade library_id to sub-folders and resource_items
  if (targetLibraryId !== undefined) {
    // BFS to collect all descendant folder IDs
    const allFolderIds = [folderId];
    let queue = [folderId];
    while (queue.length > 0) {
      const { data: children, error: childErr } = await supabase
        .from('folders')
        .select('id')
        .in('parent_id', queue);
      if (childErr) throw childErr;
      if (!children || children.length === 0) break;
      const childIds = children.map((c: any) => String(c.id));
      allFolderIds.push(...childIds);
      queue = childIds;
    }

    // Update sub-folders' library_id
    if (allFolderIds.length > 1) {
      const { error: folderErr } = await supabase
        .from('folders')
        .update({ library_id: targetLibraryId })
        .in('id', allFolderIds.slice(1));
      if (folderErr) throw folderErr;
    }

    // Update all resource_items in affected folders
    const { error: itemErr } = await supabase
      .from('resource_items')
      .update({ library_id: targetLibraryId })
      .in('folder_id', allFolderIds);
    if (itemErr) throw itemErr;
  }
}

// ─── Folder Preview ──────────────────────────────────

export async function getFolderPreview(
  folderId: string
): Promise<Array<{ resource_id: string | null; thumbnail_path: string | null; cover_image_path: string | null; mime_type: string | null }>> {
  const { data, error } = await supabase
    .from('resource_items')
    .select('resource:resources!inner(id, thumbnail_path, cover_image_path, mime_type)')
    .eq('folder_id', folderId)
    .eq('resource.is_trashed', false)
    .neq('resource.source_type', 'web')
    .limit(4);
  if (error) throw error;
  return (data || []).map((item: any) => ({
    resource_id: item.resource?.id ? String(item.resource.id) : null,
    thumbnail_path: item.resource?.thumbnail_path ?? null,
    cover_image_path: item.resource?.cover_image_path ?? null,
    mime_type: item.resource?.mime_type ?? null,
  }));
}

// Soft-delete a resource by parsed_media.id (used by PlayerPage which has media id directly).
// - Personal scope (default): sets is_trashed=true on the resource (global trash).
// - Team scope: only unlinks from the team library, keeps the resource in personal library.
export async function trashResourceByMediaId(
  mediaId: string,
  scopeType?: 'personal' | 'team',
  scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams();
  if (scopeType) params.set('scope_type', scopeType);
  if (scopeId) params.set('scope_id', scopeId);
  const query = params.toString() ? `?${params}` : '';
  const response = await fetch(
    `${apiUrl}/api/v1/resources/by-media-id/${mediaId}/trash${query}`,
    {
      method: 'POST',
      headers: await getAuthHeaders(),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Trash failed' }));
    throw new Error(err.detail || 'Failed to trash resource');
  }
}

// Soft-delete a downloaded video by moving it to the recycle bin (by platform_id).
// - Personal scope (default): sets is_trashed=true on the resource (global trash).
// - Team scope: only unlinks from the team library, keeps the resource in personal library.
export async function trashResourceByPlatformId(
  platformId: string,
  scopeType?: 'personal' | 'team',
  scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams();
  if (scopeType) params.set('scope_type', scopeType);
  if (scopeId) params.set('scope_id', scopeId);
  const query = params.toString() ? `?${params}` : '';
  const response = await fetch(
    `${apiUrl}/api/v1/resources/by-platform-id/${platformId}/trash${query}`,
    {
      method: 'POST',
      headers: await getAuthHeaders(),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Trash failed' }));
    throw new Error(err.detail || 'Failed to trash resource');
  }
}

// Unlink a downloaded video's resource from the user's scope (by media_id).
// Does NOT delete the parsed_media record or physical files.
// The DB orphan-GC trigger auto-trashes the resource if no references remain.
export async function unlinkResourceByPlatformId(
  platformId: string,
  scopeType: 'personal' | 'team' = 'personal',
  scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_type: scopeType });
  if (scopeId) params.set('scope_id', scopeId);
  const response = await fetch(
    `${apiUrl}/api/v1/resources/by-platform-id/${platformId}?${params}`,
    {
      method: 'DELETE',
      headers: await getAuthHeaders(),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Unlink failed' }));
    throw new Error(err.detail || 'Failed to unlink resource');
  }
}

// 批量删除
export async function trashResources(
  resourceIds: string[],
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
): Promise<void> {
  const apiUrl = getApiUrl();
  const headers = await getAuthHeaders();
  await Promise.all(resourceIds.map(async (id) => {
    const params = new URLSearchParams({
      scope_type: scopeType,
      scope_id: scopeId,
    });
    if (folderId) params.set('folder_id', folderId);

    const response = await fetch(`${apiUrl}/api/v1/resources/${id}?${params}`, {
      method: 'DELETE',
      headers,
    });
    if (!response.ok) throw new Error('Failed to trash resource');
  }));
}
