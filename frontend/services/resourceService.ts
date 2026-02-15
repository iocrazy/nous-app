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

// ─── Resources ──────────────────────────────────────────

export async function fetchResources(
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
  libraryId?: string | null
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources(*)')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId);

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
    .select('*', { count: 'exact', head: true })
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId);

  if (error) throw error;
  return count || 0;
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

export async function trashResource(resourceId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}`, {
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

// ─── File URL ───────────────────────────────────────────

export function getResourceFileUrl(resourceId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/file`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
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

export async function fetchSmartFolders(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<SmartCollection[]> {
  const { data, error } = await supabase
    .from('smart_collections')
    .select('*')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('is_active', true)
    .order('name', { ascending: true });

  if (error) throw error;
  return data || [];
}

/**
 * Query resources matching a smart folder's rules.
 * Builds Supabase filters from JSONB rules.
 */
export async function fetchSmartFolderResources(
  scopeType: 'personal' | 'team',
  scopeId: string,
  rules: { match: string; conditions: Array<{ field: string; operator: string; value: string }> }
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources(*)')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId);

  // Apply conditions
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
