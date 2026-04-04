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
  color?: string;
  type?: 'system' | 'user';
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
