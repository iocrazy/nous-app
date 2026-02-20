/**
 * Unified Tag Service — tag operations for Resources.
 *
 * Global tag CRUD is shared; entity-specific associations route to
 * /resources/{id}/tags endpoints.
 */

import { getAuthHeaders } from './parserService';
import type { Tag } from '../types';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

// ─── Global tag CRUD ──────────────────────────────────────

export async function fetchAllTags(): Promise<Tag[]> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to fetch tags');
  const json = await res.json();
  return json.tags ?? json.data ?? [];
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
  return res.json();
}

export async function deleteTag(tagId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to delete tag');
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
