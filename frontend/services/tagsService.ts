/**
 * Tags Service - Tag management for videos
 */

import { getAuthHeaders } from './parserService';

// API configuration
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL;
  }
  return 'http://localhost:8080';
};

// Types
export interface Tag {
  id: number;
  name: string;
  color: string | null;
  description: string | null;
  video_count: number;
  created_at: string;
}

export interface TagCreate {
  name: string;
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

  const response = await fetch(`${apiUrl}/api/v1/tags/video/${videoId}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get video tags' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.tags || [];
};

/**
 * Add tags to a video
 */
export const addTagsToVideo = async (videoId: number, tagIds: number[]): Promise<VideoTagsResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/video/${videoId}`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ tag_ids: tagIds }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to add tags' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Remove a tag from a video
 */
export const removeTagFromVideo = async (videoId: number, tagId: number): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/tags/video/${videoId}/tag/${tagId}`, {
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
  videos: Array<{ id: number; aweme_id: string; title: string }>;
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
