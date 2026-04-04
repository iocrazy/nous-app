/**
 * Smart Collection Service - Rule-based dynamic collections
 */

import { getAuthHeaders } from './parserService';
import { ParsedMedia } from '../types';
import { getApiUrl } from '../utils/apiConfig';

// Types
export interface CollectionCondition {
  field: 'tag' | 'author' | 'date' | 'title' | 'description' | 'media_type' | 'view_count';
  operator: 'equals' | 'contains' | 'starts_with' | 'in' | 'gt' | 'lt' | 'gte' | 'lte';
  value: string | number | string[];
}

export interface CollectionRules {
  match: 'all' | 'any';
  conditions: CollectionCondition[];
}

export interface SmartCollection {
  id: string;  // UUID from backend
  name: string;
  description: string | null;
  icon: string | null;
  color: string | null;
  rules: CollectionRules;
  is_preset: boolean;
  is_active: boolean;
  sort_by: string;
  sort_order: 'asc' | 'desc';
  video_count: number;
  created_at: string;
  updated_at: string;
}

export interface SmartCollectionCreate {
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  rules: CollectionRules;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export interface SmartCollectionUpdate {
  name?: string;
  description?: string;
  icon?: string;
  color?: string;
  rules?: CollectionRules;
  is_active?: boolean;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export interface SmartCollectionVideosResponse {
  media: ParsedMedia[];
  total: number;
  page: number;
  page_size: number;
  collection_id: string;  // UUID from backend
  collection_name: string;
}

/**
 * Get all smart collections for the current user
 */
export const fetchSmartCollections = async (): Promise<SmartCollection[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/collections`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch collections' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.collections || [];
};

/**
 * Get a single smart collection by ID
 */
export const getSmartCollection = async (collectionId: string): Promise<SmartCollection> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/collections/${collectionId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch collection' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Create a new smart collection
 */
export const createSmartCollection = async (
  collection: SmartCollectionCreate
): Promise<SmartCollection> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/collections`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(collection),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to create collection' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Update an existing smart collection
 */
export const updateSmartCollection = async (
  collectionId: string,
  updates: SmartCollectionUpdate
): Promise<SmartCollection> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/collections/${collectionId}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify(updates),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to update collection' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Delete a smart collection
 */
export const deleteSmartCollection = async (collectionId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/collections/${collectionId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to delete collection' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Get videos in a smart collection
 */
export const getSmartCollectionVideos = async (
  collectionId: string,
  page: number = 1,
  pageSize: number = 20,
  useCache: boolean = true
): Promise<SmartCollectionVideosResponse> => {
  const apiUrl = getApiUrl();

  const params = new URLSearchParams({
    page: page.toString(),
    page_size: pageSize.toString(),
    use_cache: useCache.toString(),
  });

  const response = await fetch(
    `${apiUrl}/api/v1/collections/${collectionId}/videos?${params}`,
    {
      method: 'GET',
      headers: await getAuthHeaders(),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch videos' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Refresh a smart collection's cached video list
 */
export const refreshSmartCollection = async (
  collectionId: string
): Promise<{ message: string; video_count: number }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/collections/${collectionId}/refresh`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to refresh collection' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Initialize preset collections for the current user
 */
export const initPresetCollections = async (): Promise<SmartCollection[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/collections/init-presets`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to init presets' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.collections || [];
};

// Helper function to build rules
export const buildCollectionRules = (
  match: 'all' | 'any',
  conditions: CollectionCondition[]
): CollectionRules => {
  return { match, conditions };
};

// Preset rule builders
export const presetRules = {
  recentVideos: (days: number = 7): CollectionRules => ({
    match: 'all',
    conditions: [
      {
        field: 'date',
        operator: 'gte',
        value: new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
      },
    ],
  }),

  byAuthor: (author: string): CollectionRules => ({
    match: 'all',
    conditions: [
      { field: 'author', operator: 'equals', value: author },
    ],
  }),

  byTag: (tagName: string): CollectionRules => ({
    match: 'all',
    conditions: [
      { field: 'tag', operator: 'equals', value: tagName },
    ],
  }),

  highViewCount: (minViews: number = 1000): CollectionRules => ({
    match: 'all',
    conditions: [
      { field: 'view_count', operator: 'gte', value: minViews },
    ],
  }),
};
