/**
 * Unified Tag Service — tag operations for Resources.
 *
 * Global tag CRUD is shared; entity-specific associations route to
 * /resources/{id}/tags endpoints.
 */

import { getAuthHeaders } from './parserService';
import type { Tag } from '../types';
import { getApiUrl } from '../utils/apiConfig';

// ─── Global tag CRUD ──────────────────────────────────────

// Module-level cache with short TTL to avoid duplicate requests
let _allTagsCache: { data: Tag[]; ts: number } | null = null;
let _allTagsPromise: Promise<Tag[]> | null = null;
const ALL_TAGS_TTL_MS = 5000; // 5 second cache

export async function fetchAllTags(): Promise<Tag[]> {
  // Return cached if fresh
  if (_allTagsCache && Date.now() - _allTagsCache.ts < ALL_TAGS_TTL_MS) {
    return _allTagsCache.data;
  }
  // Deduplicate concurrent requests
  if (_allTagsPromise) return _allTagsPromise;

  _allTagsPromise = (async () => {
    const apiUrl = getApiUrl();
    const res = await fetch(`${apiUrl}/api/v1/tags`, {
      headers: await getAuthHeaders(),
    });
    if (!res.ok) throw new Error('Failed to fetch tags');
    const json = await res.json();
    const tags = json.tags ?? json.data ?? [];
    _allTagsCache = { data: tags, ts: Date.now() };
    return tags;
  })();
  try {
    return await _allTagsPromise;
  } finally {
    _allTagsPromise = null;
  }
}

/** Invalidate tags cache (call after create/delete) */
export function invalidateAllTagsCache() {
  _allTagsCache = null;
}

export async function createTag(data: {
  name: string;
  name_zh?: string;
  color?: string;
  icon?: string;
  type?: 'system' | 'user';
  group_id?: string | null;
}): Promise<Tag> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ ...data, type: data.type || 'user' }),
  });
  if (!res.ok) throw new Error('Failed to create tag');
  invalidateAllTagsCache();
  return res.json();
}

export async function deleteTag(tagId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to delete tag');
  invalidateAllTagsCache();
}

// ─── Update tag + tag groups + statistics ────────────────
// These endpoints live on /api/v1/tags/*; consolidating them here so
// callers no longer need to import the legacy tagsService alongside.

export interface TagUpdate {
  name?: string;
  color?: string;
  icon?: string;
  enabled?: boolean;
  group_id?: string | null;
  sort_order?: number;
}

export async function updateTag(
  tagId: string,
  updates: TagUpdate,
): Promise<Tag> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: 'Failed to update tag' }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  invalidateAllTagsCache();
  return res.json();
}

export interface TagGroup {
  id: string;
  name: string;
  sort_order: number;
  created_at: string;
}

export async function fetchTagGroups(): Promise<TagGroup[]> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/groups`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: 'Failed to fetch tag groups' }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();
  return data.groups || [];
}

export async function createTagGroup(name: string): Promise<TagGroup> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/groups`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: 'Failed to create group' }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function deleteTagGroup(groupId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/groups/${groupId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: 'Failed to delete group' }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
}

export async function reorderTagGroups(groupIds: string[]): Promise<void> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/groups/reorder`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ group_ids: groupIds }),
  });
  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: 'Failed to reorder' }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
}

export interface TagStatistics {
  success: boolean;
  top_tags: {
    id: string;
    name: string;
    color: string;
    icon?: string;
    type: string;
    count: number;
  }[];
  total_tagged_videos: number;
}

export async function fetchTagStatistics(
  limit: number = 10,
): Promise<TagStatistics> {
  const apiUrl = getApiUrl();
  const res = await fetch(
    `${apiUrl}/api/v1/tags/statistics?limit=${limit}`,
    { headers: await getAuthHeaders() },
  );
  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: 'Failed to fetch tag statistics' }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ─── Resource tag associations ────────────────────────────

export interface TagAssociation {
  tag: Tag;
  confidence?: number;
  source?: string;
  tagged_by?: string;
  created_at?: string;
}

/**
 * Fetch tags for a resource.
 */
export async function fetchResourceTags(
  resourceId: string,
): Promise<TagAssociation[]> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to fetch resource tags');
  const json = await res.json();
  return (json.data ?? []).map((item: { tag: Tag }) => ({ tag: item.tag }));
}

/**
 * Add a tag to a resource.
 */
export async function addResourceTag(
  resourceId: string,
  tagId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ tag_id: tagId }),
  });
  if (!res.ok) throw new Error('Failed to add resource tag');
}

/**
 * Remove a tag from a resource.
 */
export async function removeResourceTag(
  resourceId: string,
  tagId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags/${tagId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to remove resource tag');
}
