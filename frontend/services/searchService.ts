/**
 * Search Service - Semantic and hybrid search functionality
 */

import { apiClient } from './apiClient';
import type { SearchChipFilters } from './searchChipFilters';

// Types

/** Retrieval layer a hit came from. Only `text` and `semantic` are built
 *  today; `visual` / `camera` arrive with shot indexing, `transcript` with
 *  its own phase. A result without `layer` (backend predating vector spaces)
 *  is a `text` hit. */
export type HitLayer = 'text' | 'semantic' | 'visual' | 'camera' | 'transcript';

/** Outcome of the hybrid search's vector leg — mirrors the backend's
 *  `VECTOR_LEG_OUTCOMES`. Anything but `ok` means the vector leg did not
 *  contribute, even though the response otherwise looks identical. */
export type VectorLegOutcome =
  | 'ok'
  | 'unconfigured'
  | 'embed_failed'
  | 'dimension_mismatch'
  | 'timeout'
  | 'unavailable'
  | 'error'
  | 'store_missing'
  | 'skipped_filters'
  | 'skipped_full_page'
  | 'skipped_no_scope'
  | 'skipped_no_query';

/** What a card / detail panel needs to show why an item matched. */
export interface SearchHit {
  layer: HitLayer;
  score: number;
}

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
  /** Layer of the best match for this video. Optional: absent on backends
   *  that predate vector spaces. */
  layer?: HitLayer;
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
  /** Hybrid only. Absent / null on other search types. */
  vector_leg?: VectorLegOutcome | null;
  /** Hybrid only: hit count per layer. Absent on backends that predate
   *  vector spaces — the UI draws no legs row at all in that case. */
  legs?: Partial<Record<HitLayer, number>> | null;
  reranked?: boolean;
}

export interface HybridSearchFilters {
  tag_ids?: number[];
  author?: string;
  date_from?: string;
  date_to?: string;
  min_views?: number;
  media_type?: number;
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
  // Smart Search delegates to the same RPC as keyword search on the backend,
  // so the chips apply identically. Distinct from ``filters`` above, which is
  // this endpoint's own (older, narrower) filter contract.
  chipFilters?: SearchChipFilters,
): Promise<SearchResponse> =>
  apiClient.post<SearchResponse>('/api/v1/search/hybrid', {
    query,
    limit,
    threshold,
    // Smart Search used to drop the scope checkboxes on the floor: the
    // backend hardcoded the four basic fields, so ticking Tags did nothing.
    ...(fields && fields.length > 0 ? { fields } : {}),
    ...filters,
    // Spread AFTER ``filters`` would let a stale caller's key win; it is a
    // separate key, so order is moot — but it stays last so the two filter
    // contracts read in the order they were added.
    ...(chipFilters ? { filters: chipFilters } : {}),
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
  // The caller's active filter chips. Omitted when the toolbar is untouched,
  // so a request without filters is byte-identical to what it was before the
  // chips were wired through — see services/searchChipFilters.ts for why they
  // have to be here at all.
  filters?: SearchChipFilters,
): Promise<SearchResponse> =>
  apiClient.post<SearchResponse>('/api/v1/search/text', {
    query,
    limit,
    fields: fields && fields.length > 0 ? fields : undefined,
    filters,
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

// --- Vector spaces status (Settings → AI → Vectors) ---

/** Real body of `GET /api/v1/search/vectors/status` (PR 2). `space` is null
 *  when no embedder is configured or the vector store is not migrated. */
export interface VectorsStatus {
  status: 'ok' | 'unconfigured' | 'store_missing';
  space: {
    id: string; // Snowflake BIGINT, serialised as a string by the backend (JS precision past 2^53)
    actual_model: string;
    protocol: string;
    dims: number;
    modalities: string[];
    instruction_version: string;
  } | null;
  layers: Array<{
    layer: 'semantic' | 'transcript';
    status: 'ok' | 'not_built';
    covered: number;
    total: number;
    /** Covered vectors the next backfill re-embeds; absent on older backends. */
    stale?: number;
  }>;
}

export const getVectorsStatus = (): Promise<VectorsStatus> =>
  apiClient.get<VectorsStatus>('/api/v1/search/vectors/status');
