/**
 * Collection Service - Backend API proxy for video collection operations
 *
 * All collection operations go through the backend API instead of direct Supabase calls.
 */

import { getAuthHeaders } from './parserService';
import { Collection } from '../types';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

/**
 * Fetch all collections the user has access to
 */
export const fetchMyCollections = async (): Promise<Collection[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/video-collections`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return (data.collections || []).map((c: any) => ({
    ...c,
    id: String(c.id),
  }));
};

/**
 * Create a new collection
 */
export const createCollection = async (name: string, teamId?: string): Promise<Collection> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/video-collections`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      name,
      team_id: teamId || null,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return {
    ...data,
    id: String(data.id),
  };
};

/**
 * Delete a collection
 */
export const deleteCollection = async (collectionId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/video-collections/${collectionId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok && response.status !== 204) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Add a video to a collection
 */
export const addVideoToCollection = async (
  collectionId: string,
  videoAwemeId: string
): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/video-collections/${collectionId}/videos`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      video_aweme_id: videoAwemeId,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    if (response.status === 404) {
      throw new Error('Video not found');
    }
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Remove a video from a collection
 */
export const removeVideoFromCollection = async (
  collectionId: string,
  videoAwemeId: string
): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(
    `${apiUrl}/api/v1/video-collections/${collectionId}/videos/${videoAwemeId}`,
    {
      method: 'DELETE',
      headers: getAuthHeaders(),
    }
  );

  if (!response.ok && response.status !== 204) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Get all collection IDs a video belongs to
 */
export const fetchVideoCollections = async (videoAwemeId: string): Promise<string[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(
    `${apiUrl}/api/v1/video-collections/video/${videoAwemeId}`,
    {
      method: 'GET',
      headers: getAuthHeaders(),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.collection_ids || [];
};

/**
 * Get all video aweme_ids in a collection
 */
export const fetchCollectionVideoIds = async (collectionId: string): Promise<string[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(
    `${apiUrl}/api/v1/video-collections/${collectionId}/videos`,
    {
      method: 'GET',
      headers: getAuthHeaders(),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.aweme_ids || [];
};

/**
 * Get all unique video aweme_ids from multiple collections
 */
export const fetchMultipleCollectionsVideoIds = async (collectionIds: string[]): Promise<string[]> => {
  if (collectionIds.length === 0) {
    return [];
  }

  const apiUrl = getApiUrl();

  const response = await fetch(
    `${apiUrl}/api/v1/video-collections/batch-videos`,
    {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ collection_ids: collectionIds }),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.aweme_ids || [];
};
