/**
 * Smart Collection Service - Rule-based dynamic collections
 */

import { apiClient } from './apiClient';
import { ParsedMedia } from '../types';

// Types
export interface CollectionCondition {
  field:
    | 'tag'
    | 'author'
    | 'date'
    | 'title'
    | 'description'
    | 'media_type'
    | 'view_count';
  operator:
    | 'equals'
    | 'contains'
    | 'starts_with'
    | 'in'
    | 'gt'
    | 'lt'
    | 'gte'
    | 'lte';
  value: string | number | string[];
}

export interface CollectionRules {
  match: 'all' | 'any';
  conditions: CollectionCondition[];
}

export interface SmartCollection {
  id: string; // UUID from backend
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
  collection_id: string;
  collection_name: string;
}

export const fetchSmartCollections = async (): Promise<SmartCollection[]> => {
  const data = await apiClient.get<{ collections?: SmartCollection[] }>(
    '/api/v1/collections',
  );
  return data.collections || [];
};

export const getSmartCollection = async (
  collectionId: string,
): Promise<SmartCollection> =>
  apiClient.get<SmartCollection>(`/api/v1/collections/${collectionId}`);

export const createSmartCollection = async (
  collection: SmartCollectionCreate,
): Promise<SmartCollection> =>
  apiClient.post<SmartCollection>('/api/v1/collections', collection);

export const updateSmartCollection = async (
  collectionId: string,
  updates: SmartCollectionUpdate,
): Promise<SmartCollection> =>
  apiClient.put<SmartCollection>(
    `/api/v1/collections/${collectionId}`,
    updates,
  );

export const deleteSmartCollection = async (
  collectionId: string,
): Promise<void> => {
  await apiClient.delete(`/api/v1/collections/${collectionId}`);
};

export const getSmartCollectionVideos = async (
  collectionId: string,
  page: number = 1,
  pageSize: number = 20,
  useCache: boolean = true,
): Promise<SmartCollectionVideosResponse> =>
  apiClient.get<SmartCollectionVideosResponse>(
    `/api/v1/collections/${collectionId}/videos`,
    {
      query: { page, page_size: pageSize, use_cache: useCache },
    },
  );

export const refreshSmartCollection = async (
  collectionId: string,
): Promise<{ message: string; video_count: number }> =>
  apiClient.post<{ message: string; video_count: number }>(
    `/api/v1/collections/${collectionId}/refresh`,
  );

export const initPresetCollections = async (): Promise<SmartCollection[]> => {
  const data = await apiClient.post<{ collections?: SmartCollection[] }>(
    '/api/v1/collections/init-presets',
  );
  return data.collections || [];
};

// Helper function to build rules
export const buildCollectionRules = (
  match: 'all' | 'any',
  conditions: CollectionCondition[],
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
        value: new Date(Date.now() - days * 24 * 60 * 60 * 1000)
          .toISOString()
          .split('T')[0],
      },
    ],
  }),

  byAuthor: (author: string): CollectionRules => ({
    match: 'all',
    conditions: [{ field: 'author', operator: 'equals', value: author }],
  }),

  byTag: (tagName: string): CollectionRules => ({
    match: 'all',
    conditions: [{ field: 'tag', operator: 'equals', value: tagName }],
  }),

  highViewCount: (minViews: number = 1000): CollectionRules => ({
    match: 'all',
    conditions: [{ field: 'view_count', operator: 'gte', value: minViews }],
  }),
};
