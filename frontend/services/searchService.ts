/**
 * Search Service - Semantic and hybrid search functionality
 */

import { apiClient } from './apiClient';

// Types
export interface SearchResultItem {
  media_id: number;
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
  /** Full ParsedMedia / Video rows for each hit, sorted by the same ranking
   *  as ``results``. Backend populates this so the UI can render AI-status
   *  icons / counts / audio paths on hits that aren't in the paginated
   *  in-memory library yet. Legacy callers can ignore it. */
  videos?: Array<Record<string, unknown>>;
  total: number;
  query: string;
  search_type: string;
  processing_time_ms?: number;
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
  limit: number = 100,
  threshold: number = 0.5,
): Promise<SearchResponse> =>
  apiClient.post<SearchResponse>('/api/v1/search/semantic', {
    query,
    limit,
    threshold,
  });

/**
 * Hybrid search combining semantic search with filters
 */
export const hybridSearch = async (
  query: string,
  filters: HybridSearchFilters = {},
  limit: number = 100,
  threshold: number = 0.3,
  fields?: SearchField[],
): Promise<SearchResponse> =>
  apiClient.post<SearchResponse>('/api/v1/search/hybrid', {
    query,
    limit,
    threshold,
    // Smart Search used to drop the scope checkboxes on the floor: the
    // backend hardcoded the four basic fields, so ticking Tags did nothing.
    ...(fields && fields.length > 0 ? { fields } : {}),
    ...filters,
  });

/** Eagle-style search-scope toggles. ``title``/``description``/``author``/
 *  ``hashtags`` are direct parsed_media columns. ``transcript`` searches
 *  ``resource_transcripts.full_text`` plus the legacy
 *  ``parsed_media.ai_extract_text`` column (heavyweight — transcript bodies
 *  run to tens of KB). ``tags`` joins through resource_tags → tags.name.
 *  ``notes`` searches resources.notes (per-user). When ``fields`` is omitted
 *  the backend applies its own copy of ``DEFAULT_SEARCH_FIELDS``. */
export type SearchField =
  | 'title'
  | 'description'
  | 'author'
  | 'hashtags'
  | 'transcript'
  | 'tags'
  | 'notes';

export const ALL_SEARCH_FIELDS: SearchField[] = [
  'title',
  'description',
  'author',
  'hashtags',
  'transcript',
  'tags',
  'notes',
];

/** Scopes selected by default on a fresh install.
 *
 *  The search box promises "title, tags, notes", so those three ship on —
 *  leaving ``tags``/``notes`` opt-in meant anyone who never opened the scope
 *  picker could not find a thing by tag. ``transcript`` stays opt-in: it is
 *  the one genuinely heavy scope (a join against multi-KB transcript bodies)
 *  and the placeholder never promised it.
 *
 *  Keep in sync with ``DEFAULT_SEARCH_FIELDS`` in
 *  ``backend/app/schemas/search.py`` — the backend applies its own copy when
 *  a request omits ``fields``. */
export const DEFAULT_SEARCH_FIELDS: SearchField[] = [
  'title',
  'description',
  'author',
  'hashtags',
  'tags',
  'notes',
];

/**
 * Plain-text ILIKE search — returns every row whose selected fields contain
 * the substring. No semantic ranking, no top-N cutoff (up to ``limit``,
 * backend caps at 5000). ``fields`` lets users narrow the search scope
 * Eagle-style; omitting it applies the backend's DEFAULT_SEARCH_FIELDS.
 */
export const textSearch = async (
  query: string,
  limit: number = 1000,
  fields?: SearchField[],
): Promise<SearchResponse> =>
  apiClient.post<SearchResponse>('/api/v1/search/text', {
    query,
    limit,
    fields: fields && fields.length > 0 ? fields : undefined,
  });

/**
 * Find similar videos based on a source video
 */
export const findSimilarVideos = async (
  mediaId: number,
  limit: number = 10,
  threshold: number = 0.7,
): Promise<SearchResponse> =>
  apiClient.get<SearchResponse>(`/api/v1/search/similar/${mediaId}`, {
    query: { limit, threshold },
  });

/**
 * Quick search for search bar autocomplete
 */
export const quickSearch = async (
  query: string,
  limit: number = 5,
): Promise<{
  results: SearchResultItem[];
  suggestions: QuickSearchSuggestion[];
}> =>
  apiClient.get<{
    results: SearchResultItem[];
    suggestions: QuickSearchSuggestion[];
  }>('/api/v1/search/quick', { query: { q: query, limit } });

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
        media_id: video.id || 0,
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
