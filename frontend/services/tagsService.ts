/**
 * Tags Service - Tag management for videos
 */

import { getAuthHeaders } from './parserService';

// API configuration - empty string means use relative paths (via Vite proxy)
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

// Types
export interface Tag {
  id: string;  // UUID string from backend
  name: string;
  name_zh?: string | null;  // Chinese name for bilingual support
  color: string | null;
  icon: string | null;
  type: 'system' | 'user' | 'time';
  user_id?: string;
  video_count?: number;  // Only present in some responses
  created_at: string;
}

export interface TagCreate {
  name: string;
  name_zh?: string;  // Chinese name
  color?: string;
  description?: string;
}

export interface TagUpdate {
  name?: string;
  color?: string;
  description?: string;
}

export interface VideoTagsResponse {
  video_id: number;
  tags: Tag[];
}

/**
 * Get all tags for the current user
 */
export const fetchTags = async (): Promise<Tag[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch tags' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.tags || [];
};

/**
 * Create a new tag
 */
export const createTag = async (tag: TagCreate): Promise<Tag> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags`, {
    method: 'POST',
    headers: getAuthHeaders(),
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
export const updateTag = async (tagId: number, updates: TagUpdate): Promise<Tag> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
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
export const deleteTag = async (tagId: number): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to delete tag' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Get tags for a specific video
 */
export const getVideoTags = async (videoId: number): Promise<Tag[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/videos/${videoId}/tags`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get video tags' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  // Backend returns { video_id, tags: [{ tag: {...}, confidence, source, created_at }] }
  return (data.tags || []).map((item: { tag: Tag }) => item.tag);
};

/**
 * Add tags to a video
 */
export const addTagsToVideo = async (videoId: number, tagIds: string[]): Promise<VideoTagsResponse> => {
  const apiUrl = getApiUrl();

  // Backend expects single tag at a time: { tag_id, confidence?, source? }
  // We'll add tags one by one
  for (const tagId of tagIds) {
    const response = await fetch(`${apiUrl}/api/v1/tags/videos/${videoId}/tags`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ tag_id: tagId }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to add tag' }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
  }

  // Return updated tags
  const tags = await getVideoTags(videoId);
  return { video_id: videoId, tags };
};

/**
 * Remove a tag from a video
 */
export const removeTagFromVideo = async (videoId: number, tagId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/videos/${videoId}/tags/${tagId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to remove tag' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Get videos by tag
 */
export const getVideosByTag = async (
  tagId: number,
  page: number = 1,
  pageSize: number = 20
): Promise<{
  videos: Array<{ id: number; platform_id: string; title: string }>;
  total: number;
  page: number;
  page_size: number;
}> => {
  const apiUrl = getApiUrl();

  const params = new URLSearchParams({
    page: page.toString(),
    page_size: pageSize.toString(),
  });

  const response = await fetch(`${apiUrl}/api/v1/tags/${tagId}/videos?${params}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get videos by tag' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
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
    headers: getAuthHeaders(),
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
