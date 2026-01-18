/**
 * Search Service - Semantic and hybrid search functionality
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
export interface SearchResultItem {
  video_id: number;
  aweme_id: string;
  title: string;
  cover_url: string | null;
  author: string | null;
  similarity_score: number;
  description: string | null;
  tags: string[];
  view_count: number;
  created_at: string;
}

export interface SearchResponse {
  results: SearchResultItem[];
  total: number;
  query: string;
  search_type: string;
  processing_time_ms: number;
}

export interface HybridSearchFilters {
  tag_ids?: number[];
  author?: string;
  date_from?: string;
  date_to?: string;
  min_views?: number;
  aweme_type?: number;
}

export interface QuickSearchSuggestion {
  type: 'recent' | 'popular' | 'tag';
  text: string;
  count?: number;
}

/**
 * Semantic search using natural language query
 */
export const semanticSearch = async (
  query: string,
  limit: number = 20,
  threshold: number = 0.5
): Promise<SearchResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/search/semantic`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      query,
      limit,
      threshold,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Search failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Hybrid search combining semantic search with filters
 */
export const hybridSearch = async (
  query: string,
  filters: HybridSearchFilters = {},
  limit: number = 20,
  threshold: number = 0.3
): Promise<SearchResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/search/hybrid`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      query,
      limit,
      threshold,
      ...filters,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Search failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Find similar videos based on a source video
 */
export const findSimilarVideos = async (
  videoId: number,
  limit: number = 10,
  threshold: number = 0.7
): Promise<SearchResponse> => {
  const apiUrl = getApiUrl();

  const params = new URLSearchParams({
    limit: limit.toString(),
    threshold: threshold.toString(),
  });

  const response = await fetch(
    `${apiUrl}/api/v1/search/similar/${videoId}?${params}`,
    {
      method: 'GET',
      headers: getAuthHeaders(),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Search failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Quick search for search bar autocomplete
 */
export const quickSearch = async (
  query: string,
  limit: number = 5
): Promise<{
  results: SearchResultItem[];
  suggestions: QuickSearchSuggestion[];
}> => {
  const apiUrl = getApiUrl();

  const params = new URLSearchParams({
    q: query,
    limit: limit.toString(),
  });

  const response = await fetch(`${apiUrl}/api/v1/search/quick?${params}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Search failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};
