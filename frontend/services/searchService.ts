/**
 * Search Service - Semantic and hybrid search functionality
 */

import { apiClient } from './apiClient';
import type { NousModelPublic } from '../types/api';
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
  /** `visual` hits: the matched shot's span (ms) — the moment to play from
   *  and the segment the card's position bar marks. */
  startMs?: number;
  endMs?: number;
  /** An `index_shots` task for this video is queued / running (Task Center),
   *  so the visual leg will start seeing it shortly. */
  shotsQueued?: boolean;
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
  /** `visual` hits only: the matching shot (the moment to play from).
   *  `shot_id` is a Snowflake, a string on the wire. */
  shot?: SearchHitShot | null;
}

export interface SearchHitShot {
  shot_id: string;
  start_ms: number;
  end_ms: number;
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
  /** Hybrid only: what the visual (shot frame) leg did, same codes as
   *  `vector_leg`; absent / null when the leg was not part of the search. */
  visual_leg?: VectorLegOutcome | null;
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

/** One embedding space (`SpaceInfo`). The id is a Snowflake BIGINT the
 *  backend serialises as a string (JS precision past 2^53). */
export interface VectorSpaceInfo {
  id: string;
  actual_model: string;
  protocol: string;
  dims: number;
  modalities: string[];
  instruction_version: string;
}

export interface VectorLayerStatus {
  layer: 'semantic' | 'visual' | 'transcript';
  status: 'ok' | 'not_built';
  covered: number;
  total: number;
  /** Covered vectors the next backfill re-embeds; absent on older backends. */
  stale?: number;
}

/** A space with the caller's coverage in it (`SpaceStatus`). `active` = the
 *  admin-governance embedder writes and searches here; the rest are
 *  candidates. `catalog_name` is null when the model left the catalog. */
export interface VectorSpaceStatus extends VectorSpaceInfo {
  active: boolean;
  catalog_name: string | null;
  layers: VectorLayerStatus[];
}

/** Real body of `GET /api/v1/search/vectors/status`. `space` / `layers` are
 *  the ACTIVE space (null when no embedder is configured or the store is not
 *  migrated); `spaces` lists every space; `can_manage` = the caller is an
 *  admin. The last two are absent on backends before space switching. */
export interface VectorsStatus {
  status: 'ok' | 'unconfigured' | 'store_missing';
  space: VectorSpaceInfo | null;
  layers: VectorLayerStatus[];
  spaces?: VectorSpaceStatus[];
  can_manage?: boolean;
}

/** Body of `DELETE /api/v1/search/vectors/spaces/{id}`. `deleted_vectors`
 *  counts every user's vectors the delete cascaded away. */
export interface DeleteVectorSpaceResult {
  deleted: boolean;
  space_id: string;
  deleted_vectors: number;
}

export const getVectorsStatus = (): Promise<VectorsStatus> =>
  apiClient.get<VectorsStatus>('/api/v1/search/vectors/status');

/** Add Space (admin). Probes the catalog model first: a 422 carries
 *  `details: {code: 'dimension_mismatch', expected, got, model}`, an
 *  unreachable provider is a 502 `provider_error`. */
export const createVectorSpace = (modelName: string): Promise<VectorSpaceInfo> =>
  apiClient.post<VectorSpaceInfo>('/api/v1/search/vectors/spaces', { model_name: modelName });

/** Switch (admin): make this space active. Answers with the new status. */
export const activateVectorSpace = (spaceId: string): Promise<VectorsStatus> =>
  apiClient.post<VectorsStatus>(`/api/v1/search/vectors/spaces/${encodeURIComponent(spaceId)}/activate`);

/** Delete a non-active space and every vector in it (admin). */
export const deleteVectorSpace = (spaceId: string): Promise<DeleteVectorSpaceResult> =>
  apiClient.delete<DeleteVectorSpaceResult>(`/api/v1/search/vectors/spaces/${encodeURIComponent(spaceId)}`);

/** Catalog models Add Space may pick: enabled PLATFORM embedding rows only.
 *  (`/ai/nous-models` also returns the caller's own BYOK rows.) */
export const getVectorSpaceCatalog = async (): Promise<NousModelPublic[]> =>
  (await apiClient.get<{ models: NousModelPublic[] }>('/api/v1/search/vectors/catalog')).models;
