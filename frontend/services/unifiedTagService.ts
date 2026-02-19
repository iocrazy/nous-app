/**
 * Unified Tag Service — abstracts tag operations for both Resources and Media (Downloads).
 *
 * Global tag CRUD is shared; entity-specific associations route to the correct
 * junction table (resource_tags vs video_tags) via a TaggableType discriminator.
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

// ─── Global tag CRUD (shared by all entity types) ──────────

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

// ─── Entity-specific tag associations ──────────────────────

export type TaggableType = 'resource' | 'media';

export interface TagAssociation {
  tag: Tag;
  confidence?: number;
  source?: string;
  tagged_by?: string;
  created_at?: string;
}

/**
 * Fetch tags for any entity type.
 */
export async function fetchEntityTags(
  entityType: TaggableType,
  entityId: string,
): Promise<TagAssociation[]> {
  const apiUrl = getApiUrl();
  const url =
    entityType === 'resource'
      ? `${apiUrl}/api/v1/resources/${entityId}/tags`
      : `${apiUrl}/api/v1/tags/videos/${entityId}/tags`;

  const res = await fetch(url, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`Failed to fetch ${entityType} tags`);
  const json = await res.json();

  // Normalize: resource returns { data: [{tag}] }, video returns { tags: [{tag, confidence, source}] }
  if (entityType === 'resource') {
    return (json.data ?? []).map((item: { tag: Tag }) => ({ tag: item.tag }));
  }
  return (json.tags ?? []).map((item: { tag: Tag; confidence?: number; source?: string }) => ({
    tag: item.tag,
    confidence: item.confidence,
    source: item.source,
  }));
}

/**
 * Add a tag to any entity.
 */
export async function addEntityTag(
  entityType: TaggableType,
  entityId: string,
  tagId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const url =
    entityType === 'resource'
      ? `${apiUrl}/api/v1/resources/${entityId}/tags`
      : `${apiUrl}/api/v1/tags/videos/${entityId}/tags`;

  const res = await fetch(url, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ tag_id: tagId }),
  });
  if (!res.ok) throw new Error(`Failed to add ${entityType} tag`);
}

/**
 * Remove a tag from any entity.
 */
export async function removeEntityTag(
  entityType: TaggableType,
  entityId: string,
  tagId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const url =
    entityType === 'resource'
      ? `${apiUrl}/api/v1/resources/${entityId}/tags/${tagId}`
      : `${apiUrl}/api/v1/tags/videos/${entityId}/tags/${tagId}`;

  const res = await fetch(url, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to remove ${entityType} tag`);
}
