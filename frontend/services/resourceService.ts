import { supabase } from '../supabaseClient';
import { Folder, Resource, ResourceItem, ResourceVersion, SmartCollection } from '../types';
import { getAuthHeaders } from './parserService';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

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

export async function trashFolder(id: string): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({ is_trashed: true, trashed_at: new Date().toISOString() })
    .eq('id', id);

  if (error) throw error;
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
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    resource_id: resourceId,
    scope_type: scopeType,
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);

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
  _scopeType?: 'personal' | 'team',
  _scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}`, {
    method: 'PATCH',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ is_trashed: true }),
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

// ─── Trashed resources ───────────────────────────────────

export async function fetchTrashedResources(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<ResourceItem[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_type: scopeType, scope_id: scopeId });
  const response = await fetch(`${apiUrl}/api/v1/resources/trash?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch trashed resources');
  const json = await response.json();
  return json.data || [];
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

// 移动文件夹
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

// Soft-delete a downloaded video by moving it to the recycle bin (by platform_id).
export async function trashResourceByPlatformId(
  platformId: string,
  scopeType: 'personal' | 'team' = 'personal',
  scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_type: scopeType });
  if (scopeId) params.set('scope_id', scopeId);
  const response = await fetch(
    `${apiUrl}/api/v1/resources/by-platform-id/${platformId}/trash?${params}`,
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
): Promise<void> {
  const apiUrl = getApiUrl();
  const headers = await getAuthHeaders();
  await Promise.all(resourceIds.map(async (id) => {
    const response = await fetch(`${apiUrl}/api/v1/resources/${id}`, {
      method: 'PATCH',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_trashed: true }),
    });
    if (!response.ok) throw new Error('Failed to trash resource');
  }));
}
