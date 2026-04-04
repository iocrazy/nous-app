/**
 * Tags Service - Global tag CRUD operations.
 *
 * Resource-specific tag associations are in unifiedTagService.ts.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

// Types
export interface Tag {
  id: string;  // Snowflake BIGINT as string
  name: string;
  name_zh?: string | null;  // Chinese name for bilingual support
  color: string | null;
  icon: string | null;
  type: 'system' | 'user' | 'time';
  user_id?: string;
  group_id?: string | null;
  group_name?: string | null;
  enabled?: boolean;
  media_count?: number;
  created_at: string;
}

export interface TagGroup {
  id: string;
  name: string;
  sort_order: number;
}

export interface TagCreate {
  name: string;
  name_zh?: string;  // Chinese name
  color?: string;
}

export interface TagUpdate {
  name?: string;
  color?: string;
  icon?: string;
  enabled?: boolean;
}

/**
 * Get all tags for the current user.
 * @param enabledOnly - if true, only returns enabled tags (for Shortcuts/public API)
 */
export const fetchTags = async (enabledOnly = false): Promise<Tag[]> => {
  const apiUrl = getApiUrl();
  const params = enabledOnly ? '?enabled_only=true' : '';

  const response = await fetch(`${apiUrl}/api/v1/tags${params}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch tags' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.tags || [];
};

/**
 * Get all tag groups
 */
export const fetchTagGroups = async (): Promise<TagGroup[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/groups`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch tag groups' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.groups || [];
};

/**
 * Create a new tag
 */
export const createTag = async (tag: TagCreate): Promise<Tag> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(tag),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to create tag' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Update an existing tag
 */
export const updateTag = async (tagId: string, updates: TagUpdate): Promise<Tag> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify(updates),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to update tag' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Delete a tag
 */
export const deleteTag = async (tagId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to delete tag' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Tag statistics response
 */
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

/**
 * Get tag usage statistics
 */
export const fetchTagStatistics = async (limit: number = 10): Promise<TagStatistics> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/statistics?limit=${limit}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch tag statistics' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

// Utility functions
export const generateTagColor = (): string => {
  const colors = [
    '#ef4444', // red
    '#f97316', // orange
    '#eab308', // yellow
    '#22c55e', // green
    '#14b8a6', // teal
    '#3b82f6', // blue
    '#8b5cf6', // violet
    '#ec4899', // pink
  ];
  return colors[Math.floor(Math.random() * colors.length)];
};
