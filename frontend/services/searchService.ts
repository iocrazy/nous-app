/**
 * Search Service - Semantic and hybrid search functionality
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
export interface SearchResultItem {
  video_id: number;
  platform_id: string;
  title: string;
  cover_url: string | null;
  author: string | null;
  similarity_score: number;
  description: string | null;
  tags: string[];
  view_count: number;
  created_at: string;
}

// Type alias for backwards compatibility
export type SearchResult = SearchResultItem;

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
  media_type?: number;
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

/**
 * Local search - searches through already loaded library data (instant, no network)
 * Use this for fast text search when library is already loaded
 */
export const localSearch = (
  query: string,
  library: Array<{
    id?: number;
    platform_id: string;
    title?: string;
    description?: string;
    author?: string;
    hashtags?: string;
    cover_url?: string;
    view_count?: number;
    created_at?: string;
    tags?: string[];
  }>,
  limit: number = 20
): SearchResponse => {
  if (!query || !query.trim()) {
    return {
      results: [],
      total: 0,
      query: '',
      search_type: 'local',
      processing_time_ms: 0,
    };
  }

  const startTime = performance.now();

  // Normalize query (remove spaces for CJK text matching)
  const queryLower = query.toLowerCase();
  const queryNormalized = queryLower.replace(/\s+/g, '');

  const results: SearchResultItem[] = [];

  for (const video of library) {
    // Get searchable fields
    const title = (video.title || '').toLowerCase();
    const desc = (video.description || '').toLowerCase();
    const author = (video.author || '').toLowerCase();
    const hashtags = (video.hashtags || '').toLowerCase();
    // Also search through user-added tags
    const tags = (video.tags || []).join(' ').toLowerCase();

    // Normalized versions (no spaces)
    const titleNorm = title.replace(/\s+/g, '');
    const descNorm = desc.replace(/\s+/g, '');
    const tagsNorm = tags.replace(/\s+/g, '');

    // Check if query matches any field (including tags)
    const match =
      title.includes(queryLower) ||
      desc.includes(queryLower) ||
      author.includes(queryLower) ||
      hashtags.includes(queryLower) ||
      tags.includes(queryLower) ||
      titleNorm.includes(queryNormalized) ||
      descNorm.includes(queryNormalized) ||
      tagsNorm.includes(queryNormalized);

    if (match) {
      results.push({
        video_id: video.id || 0,
        platform_id: video.platform_id,
        title: video.title || '',
        description: video.description || null,
        cover_url: video.cover_url || null,
        author: video.author || null,
        similarity_score: 0.5,
        tags: video.tags || [],
        view_count: video.view_count || 0,
        created_at: video.created_at || '',
      });

      if (results.length >= limit) break;
    }
  }

  const processingTime = performance.now() - startTime;

  return {
    results,
    total: results.length,
    query,
    search_type: 'local',
    processing_time_ms: Math.round(processingTime),
  };
};
