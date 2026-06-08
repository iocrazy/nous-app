import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import { ParsedMedia } from '../types';
import { apiClient } from './apiClient';
import {
  applyKeysetCursor,
  sliceKeysetPage,
  type KeysetCursor,
} from './pagination';

const TABLE_NAME = 'parsed_media';
const VIEW_NAME = 'parsed_media';  // View dropped; query base table directly

// Use parsed_media!inner(*) — specific column selection caused slow query plans
// on self-hosted PostgREST. The payload difference is negligible for 20 rows.
// PR-B: download statuses live on parsed_media (the canonical shared
// source). The resources row no longer carries mirrors. ``flattenResource
// Media`` reads everything from ``parsed_media!inner(*)``.
const RESOURCE_LIST_SELECT = 'id, created_at, parsed_media!inner(*)';

import { getApiUrl } from '../utils/apiConfig';

/**
 * Frontend config interface (for Supabase URL and Anon Key)
 */
export interface FrontendConfig {
  supabase_url: string | null;
  supabase_anon_key: string | null;
  default_download_path: string | null;
  // Transcode settings
  transcode_enabled: boolean | null;
  transcode_tiers: string | null;
  ffmpeg_encoder: string | null;
  ffmpeg_preset: string | null;
  transcode_parallel_tiers: boolean | null;
}

/**
 * Fetch frontend config from backend YAML
 */
export const fetchFrontendConfig = async (): Promise<FrontendConfig | null> => {
  try {
    return await apiClient.get<FrontendConfig>('/api/v1/config');
  } catch (error) {
    console.error('Error fetching frontend config:', error);
    return null;
  }
};

/**
 * Save frontend config to backend YAML (admin only; requires auth)
 */
export const saveFrontendConfig = async (config: {
  supabase_url?: string;
  supabase_anon_key?: string;
  default_download_path?: string;
  transcode_enabled?: boolean;
  transcode_tiers?: string;
  ffmpeg_encoder?: string;
  ffmpeg_preset?: string;
  transcode_parallel_tiers?: boolean;
}): Promise<FrontendConfig | null> =>
  apiClient.put<FrontendConfig>('/api/v1/config', config);

/**
 * Get video download URL via backend API
 */
export const getDownloadUrl = (platformId: string): string => {
  return `${getApiUrl()}/api/v1/media/download/${platformId}`;
};

/**
 * Get cover download URL
 */
export const getCoverDownloadUrl = (platformId: string): string => {
  return `${getApiUrl()}/api/v1/media/download/${platformId}/cover`;
};

/**
 * Get music/audio download URL
 */
export const getMusicDownloadUrl = (platformId: string): string => {
  return `${getApiUrl()}/api/v1/media/download/${platformId}/music`;
};

/**
 * Get gallery zip download URL (all slides in one file, for image-text
 * / carousel / 动图 posts). Backend packages whatever is on disk.
 */
export const getGalleryZipUrl = (platformId: string): string => {
  return `${getApiUrl()}/api/v1/media/download/${platformId}/gallery`;
};

/**
 * Mark downloads stuck in 'downloading' for too long as 'failed'.
 * Called once on library load to clean up stale records.
 */
export const cleanupStaleDownloads = async (timeoutMinutes: number = 30): Promise<number> => {
  try {
    const result = await apiClient.post<{ cleaned?: number }>(
      '/api/v1/media/cleanup-stale-downloads',
      undefined,
      { query: { timeout_minutes: timeoutMinutes } },
    );
    return result.cleaned || 0;
  } catch {
    return 0;
  }
};

/**
 * Merge a resources row (with nested parsed_media from !inner join)
 * into a flat ParsedMedia object. Download statuses live on parsed_media
 * (canonical) — the resources table no longer mirrors them.
 */
function flattenResourceMedia(row: any): ParsedMedia {
  const pm = row.parsed_media || {};
  return {
    ...pm,
    resource_id: String(row.id),
  };
}

/**
 * Fetch a single video by platform_id (through resources table)
 */
export const fetchVideoByPlatformId = async (platformId: string): Promise<ParsedMedia | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return null;
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.user) return null;

    const { data, error } = await supabase
      .from('resources')
      .select('id, parsed_media!inner(*)')
      .eq('creator_id', session.user.id)
      .eq('source_type', 'web')
      .eq('parsed_media.platform_id', platformId)
      .maybeSingle();

    if (error) {
      console.error('Failed to fetch video:', error);
      return null;
    }

    if (!data) return null;
    return flattenResourceMedia(data);
  } catch (e) {
    console.error('Error fetching video by platform_id:', e);
    return null;
  }
};

// Keep old name as alias
export const fetchVideoByAwemeId = fetchVideoByPlatformId;

/**
 * L1 dedup probe — does the current user already own a completed
 * download for this URL? Hits Supabase directly (no backend round-trip)
 * so the parse page can short-circuit before POST /api/v1/media/fetch.
 *
 * Returns the matched ParsedMedia (with resource_id flattened) when
 * the user owns it AND the global download is complete; otherwise null.
 *
 * The check pairs with the L2 backstop in handle_media_fetch_dispatch — non-
 * browser callers (Shortcuts / API / extension) bypass this layer and
 * land on L2 instead.
 */
export const findOwnedVideoByUrl = async (url: string): Promise<ParsedMedia | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) return null;
  if (!url || !url.trim()) return null;

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.user) return null;

    const { data, error } = await supabase
      .from('resources')
      .select('id, parsed_media!inner(*)')
      .eq('creator_id', session.user.id)
      .eq('source_type', 'web')
      .eq('parsed_media.original_url', url.trim())
      .limit(1)
      .maybeSingle();

    if (error || !data) return null;

    const flat = flattenResourceMedia(data);
    const mt = String(flat.media_type ?? '');
    const isImage = ['2', '68', 'image', 'images'].includes(mt);
    const statusField = isImage ? 'image_download_status' : 'video_download_status';
    if ((flat as unknown as Record<string, unknown>)[statusField] !== 'completed') return null;
    return flat;
  } catch {
    return null;
  }
};

/**
 * Fetch a single video by id (Snowflake BIGINT — parsed_media.id)
 */
export const fetchVideoByDisplayId = async (displayId: string): Promise<ParsedMedia | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return null;
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.user) return null;

    const { data, error } = await supabase
      .from('resources')
      .select('id, parsed_media!inner(*)')
      .eq('creator_id', session.user.id)
      .eq('source_type', 'web')
      .eq('parsed_media.id', displayId)
      .maybeSingle();

    if (error) {
      console.error('Failed to fetch video by id:', error);
      return null;
    }

    if (!data) return null;
    return flattenResourceMedia(data);
  } catch (e) {
    console.error('Error fetching video by id:', e);
    return null;
  }
};

/** Pagination config — keep small for fast first paint, load more on scroll */
const PAGE_SIZE = 20;
const LOCAL_CACHE_SIZE = 500;

/** Composite keyset cursor (created_at + id). Needed because the
 *  resources.created_at column has timestamp ties (batch crawl inserts
 *  ~100 rows in the same millisecond), and a strict `created_at < cursor`
 *  predicate would silently skip every row sharing the boundary
 *  timestamp. The id tiebreaker makes pagination stable regardless of
 *  duplicate timestamps.
 */
/** The Downloads cursor is just the shared keyset cursor. Alias kept so
 *  existing imports of `LibraryCursor` keep working. */
export type LibraryCursor = KeysetCursor;

export interface PaginatedResult<T> {
  data: T[];
  totalCount: number;
  hasMore: boolean;
  page: number;
  /** Cursor for the next page — pass back as ``cursor`` to fetch the
   *  following slice. Null when there are no more pages. */
  nextCursor: LibraryCursor | null;
}

/**
 * Filter params accepted by fetchLibraryPaginated. Mirrors the subset
 * of the Resources filter bar chips that make sense for the Downloads
 * view (all of them except ``type``, since every row here is web-
 * downloaded media).
 *
 * Semantics match FetchResourcesParams (resourceService.ts): the base
 * table is ``resources`` (not ``resource_items``), so filters address
 * resources.* directly and parsed_media columns via the embedded join.
 */
export interface FetchLibraryFilterParams {
  /** AND-semantic tag ids. Applied via a 2-step resource_id
   *  intersection. */
  tag_ids?: string[];
  /** resources.rating >= min. */
  min_rating?: number;
  /** parsed_media.source_platform IN (...). Requires inner join. */
  platforms?: string[];
  /** Broad type filter (matches parsed_media.media_type). OR across the set.
   *  Used by the Type chip on My Downloads — every row in the library has a
   *  parsed_media row, so we can filter on its media_type column directly. */
  media_types?: Array<'video' | 'image' | 'audio' | 'document' | 'other'>;
  /** ai status flags. */
  ai_transcribed?: boolean;
  ai_summarized?: boolean;
  ai_analyzed?: boolean;
  /** resources.created_at inclusive bounds (YYYY-MM-DD). */
  created_after?: string;
  created_before?: string;
  /** resources.duration_seconds inclusive bounds. */
  duration_min?: number;
  duration_max?: number;
  /** aspect_bucket IN (wire values: "9:16"/"16:9"/"1:1"/"4:3"/"other"). */
  aspect_ratios?: string[];
  /** parsed_media.*_count thresholds. Requires inner join. */
  min_likes?: number;
  min_comments?: number;
  min_favorites?: number;
  min_shares?: number;
  social_combine?: 'and' | 'or';
  /** parsed_media.comment_count > 0. AND-on-top floor. */
  has_comments?: boolean;
}

/**
 * parsed_media.media_type wire values that map to each broad type chip.
 * Source of truth: ``frontend/utils/awemeType.ts::MEDIA_TYPE_MAP``. Legacy
 * numeric strings ('0','2','4','61','68') and the modern enum strings
 * ('video','carousel','image_text','special','short','live_clip') coexist
 * in the column; we list both so existing rows still match.
 */
const MEDIA_TYPE_VALUES_BY_TYPE: Record<string, string[]> = {
  video: ['video', 'special', 'short', 'live_clip', '0', '4', '61'],
  image: ['carousel', 'image_text', '2', '68'],
  // qishui (Soda) audio sets parsed_media.media_type='audio'. Was empty (no
  // audio producers existed pre-Soda) → the Audio chip forced an __impossible__
  // query → "No content" even when audio downloads exist.
  audio: ['audio'],
  document: [],
  other: [],
};

/**
 * Map the Type chip's broad groups to the `parsed_media.media_type` wire
 * values used by both the PostgREST path (`applyLibraryFilters`) and the
 * RPC path (`fetchLibraryViaRpc`). Returns:
 *   - null  when no type filter is active (caller skips the filter)
 *   - ['__impossible__'] when an active group maps to no wire values
 *     (document/other in the web library) — forces zero rows rather than
 *     silently returning the unfiltered list
 *   - the flattened wire-value list otherwise
 * Single source of truth so the two query paths can never disagree.
 */
export function mediaTypesToWire(
  mediaTypes: string[] | undefined,
): string[] | null {
  if (!mediaTypes || mediaTypes.length === 0) return null;
  const wire = mediaTypes.flatMap((t) => MEDIA_TYPE_VALUES_BY_TYPE[t] ?? []);
  return wire.length > 0 ? wire : ['__impossible__'];
}

/**
 * Server-side filtered + keyset-paginated Downloads search (mig 275 RPC).
 * Used for the tag-filtered case so tag-AND scales past PostgREST's 1000-row
 * cap and never builds a giant `.in()` URL. Returns the same
 * `PaginatedResult<ParsedMedia>` shape as `fetchLibraryPaginated`; the RPC
 * rows carry `{ id: resources.id, created_at: resources.created_at,
 * parsed_media: {...} }` so `flattenResourceMedia` + `sliceKeysetPage`
 * treat them identically to the PostgREST rows. bigIntSafeFetch keeps
 * Snowflake ids precision-safe inside the jsonb payload.
 */
async function fetchLibraryViaRpc(
  supabase: NonNullable<ReturnType<typeof getSupabaseClient>>,
  userId: string,
  f: FetchLibraryFilterParams,
  page: number,
  pageSize: number,
  cursor: LibraryCursor | null,
  signal?: AbortSignal,
): Promise<PaginatedResult<ParsedMedia>> {
  // Map the Type chip's broad groups to parsed_media.media_type wire values —
  // MEDIA_TYPE_VALUES_BY_TYPE is the single source of truth (shared with the
  // PostgREST path). An active type filter that maps to no wire values
  // (document/other in the web library) forces zero rows via '__impossible__',
  // exactly as applyLibraryFilters does.
  const mediaTypeWire = mediaTypesToWire(f.media_types);

  let req = supabase.rpc('rpc_downloads_library_search', {
    p_user_id: userId,
    p_tag_ids: f.tag_ids ?? null,
    p_min_rating: f.min_rating ?? null,
    p_ai_transcribed: f.ai_transcribed ?? null,
    p_ai_summarized: f.ai_summarized ?? null,
    p_ai_analyzed: f.ai_analyzed ?? null,
    p_created_after: f.created_after ?? null,
    p_created_before: f.created_before ?? null,
    p_duration_min: f.duration_min ?? null,
    p_duration_max: f.duration_max ?? null,
    p_aspect_ratios: f.aspect_ratios ?? null,
    p_platforms: f.platforms ?? null,
    p_media_types: mediaTypeWire,
    p_has_comments: f.has_comments ?? null,
    p_min_likes: f.min_likes ?? null,
    p_min_comments: f.min_comments ?? null,
    p_min_favorites: f.min_favorites ?? null,
    p_min_shares: f.min_shares ?? null,
    p_social_combine: f.social_combine ?? 'and',
    p_cursor_ts: cursor?.ts ?? null,
    p_cursor_id: cursor?.id ?? null,
    p_limit: pageSize + 1,
    // Count only on the first page (cursor === null), matching the PostgREST
    // path's isFirstPage gate so totalCount reflects the active filter set.
    p_with_count: cursor === null,
  });
  if (signal) req = req.abortSignal(signal);

  const { data, error } = await req;
  if (error) throw error;

  const result = (data ?? { rows: [], total_count: null }) as {
    rows: any[];
    total_count: number | null;
  };
  const { data: pageData, hasMore, nextCursor } = sliceKeysetPage(
    result.rows ?? [],
    pageSize,
    (row) => {
      const r = row as { id?: string | number; created_at?: string };
      return r.created_at && r.id != null
        ? { ts: r.created_at, id: String(r.id) }
        : null;
    },
  );
  return {
    data: pageData.map(flattenResourceMedia) as ParsedMedia[],
    totalCount: result.total_count ?? -1,
    hasMore,
    page,
    nextCursor,
  };
}

/**
 * Fetch video library (paginated) — queries through resources table.
 *
 * Cursor-based pagination: pass ``cursor=null`` for the first page, then
 * forward ``result.nextCursor`` to subsequent calls. Falls back to
 * page-based offset for legacy callers passing a ``page`` number, but
 * cursor is preferred — it's O(1) regardless of how deep the user has
 * scrolled, while OFFSET is O(skip) and gets noticeable past page ~50.
 *
 * Filters are pushed to PostgREST server-side; the Downloads view relies
 * on this for correct pagination + totalCount.
 */
export const fetchLibraryPaginated = async (
  page: number = 0,
  pageSize: number = PAGE_SIZE,
  signal?: AbortSignal,
  filters?: FetchLibraryFilterParams,
  cursor?: LibraryCursor | null,
): Promise<PaginatedResult<ParsedMedia>> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    // Use getSession (reads localStorage) instead of getUser (API call) for speed
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.user) {
      throw new Error("User not authenticated");
    }
    const userId = session.user.id;

    const f = filters ?? {};
    const useCursor = cursor !== undefined; // explicit cursor mode (null = first page)

    // Tag-filtered → run the whole filtered + keyset query server-side via the
    // rpc_downloads_library_search RPC (mig 275). The old client path resolved
    // tags to a resource_id set with per-tag `.eq('tag_id')` SELECTs (each
    // silently capped at PostgREST's 1000-row ceiling → WRONG intersection once
    // a tag has >1000 resources) then passed it back as `.in('id', [huge list])`
    // (Kong/nginx 502 risk). The no-tag path below stays on the PostgREST keyset
    // query — already scale-safe (keyset on resources, no `.in()`).
    if (f.tag_ids && f.tag_ids.length > 0) {
      return await fetchLibraryViaRpc(
        supabase,
        userId,
        f,
        page,
        pageSize,
        cursor ?? null,
        signal,
      );
    }
    // No tag filter → no resource_id intersection to apply downstream.
    const tagResourceIds: string[] | null = null;

    const socialMetricActive =
      (f.min_likes !== undefined && f.min_likes > 0) ||
      (f.min_comments !== undefined && f.min_comments > 0) ||
      (f.min_favorites !== undefined && f.min_favorites > 0) ||
      (f.min_shares !== undefined && f.min_shares > 0);
    const needsMediaInner =
      (f.platforms && f.platforms.length > 0) ||
      (f.media_types && f.media_types.length > 0) ||
      socialMetricActive ||
      Boolean(f.has_comments);
    const mediaJoinToken = needsMediaInner ? 'parsed_media!inner' : 'parsed_media!inner';
    // NOTE: DownloadsView always requires a parsed_media row (source_type='web'
    // means the resource was parsed from a URL), so the join is !inner
    // unconditionally. Kept the token name for parity with resourceService.
    // PR-B: download statuses live on parsed_media (canonical). Drop
    // resource-level mirrors from the select; flattenResourceMedia
    // falls back to ``pm.*`` for these columns.
    const select = `id, created_at, ${mediaJoinToken}(*)`;

    // Build the paged data query. ORDER BY (created_at DESC, id DESC) so
    // ties in created_at have a stable secondary order for keyset paging.
    let query = supabase
      .from('resources')
      .select(select)
      .eq('creator_id', userId)
      .eq('source_type', 'web')
      .eq('is_trashed', false)
      .order('created_at', { ascending: false })
      .order('id', { ascending: false });

    if (useCursor) {
      // Composite keyset paging — see services/pagination.ts. The id
      // tiebreaker is what prevents the historical 417→340 row loss when
      // many rows share one created_at (batch insert at a single tick).
      query = applyKeysetCursor(query, cursor ?? null, pageSize);
    } else {
      // Legacy offset mode (kept for any caller that hasn't migrated).
      const from = page * pageSize;
      query = query.range(from, from + pageSize);
    }
    query = applyLibraryFilters(query, f, tagResourceIds);
    if (signal) query = query.abortSignal(signal);
    const { data, error } = await query;

    if (error) throw error;

    const rows = data || [];
    // Split into page + nextCursor. cursorOf reads created_at/id from the raw
    // row BEFORE flattenResourceMedia (below, at the return) overwrites
    // created_at — which belongs to resources, not the embedded parsed_media.
    const { data: pageData, hasMore, nextCursor } = sliceKeysetPage(
      rows,
      pageSize,
      (row) => {
        const r = row as { id?: string | number; created_at?: string };
        return r.created_at && r.id != null
          ? { ts: r.created_at, id: String(r.id) }
          : null;
      },
    );

    // Fast total count (only on first page — cursor=null OR page=0). Duplicate
    // the filter set so the count reflects what the user is seeing.
    let totalCount = -1;
    const isFirstPage = useCursor ? cursor == null : page === 0;
    if (isFirstPage) {
      let countQuery = supabase
        .from('resources')
        .select(`id, ${mediaJoinToken}()`, { count: 'exact', head: true })
        .eq('creator_id', userId)
        .eq('source_type', 'web')
        .eq('is_trashed', false);
      countQuery = applyLibraryFilters(countQuery, f, tagResourceIds);
      const { count } = await countQuery;
      totalCount = count ?? -1;
    }

    return {
      data: pageData.map(flattenResourceMedia) as ParsedMedia[],
      totalCount,
      hasMore,
      page,
      nextCursor,
    };
  } catch (err: any) {
    // Recognise AbortError — useLibrary cancels the in-flight request on
    // every reload / loadMore / filter change. That's expected, not a
    // failure, so log at debug (still visible in DevTools verbose mode
    // but doesn't fire console.error + the React fiber-unwind cascade)
    // and re-throw so callers can ``catch`` and skip state updates.
    //
    // 2026-05-13: prod showed 284× `Library query failed: AbortError`
    // console.error on a single library load — `err.name === 'AbortError'`
    // didn't match. postgrest-js sometimes re-throws the AbortError as
    // itself (name preserved), but other times wraps it in a plain Error
    // whose `name` is undefined but `message` still carries
    // "AbortError: signal is aborted without reason". Recognise the
    // wrapped form via the signal flag + a message-substring check;
    // useLibrary already did the same defensively at its catch level,
    // but by then the console.error had already fired here.
    const isAbort =
      err?.name === 'AbortError' ||
      Boolean(signal?.aborted) ||
      (typeof err?.message === 'string' && err.message.includes('aborted'));
    if (isAbort) {
      console.debug('Library query aborted (in-flight cancelled by next load)');
      throw err;
    }
    console.error('Library query failed:', err?.message || err);
    throw err;
  }
};

/**
 * Apply the filter-bar-derived WHERE clauses to a resources query.
 * Shared by the data + count queries above so the reported totalCount
 * never disagrees with the list.
 */
function applyLibraryFilters<T extends { [k: string]: any }>(
  q: T,
  f: FetchLibraryFilterParams,
  tagResourceIds: string[] | null,
): T {
  let query = q;
  if (tagResourceIds !== null) {
    query = query.in('id', tagResourceIds);
  }
  if (f.min_rating !== undefined && f.min_rating > 0) {
    query = query.gte('rating', f.min_rating);
  }
  if (f.ai_transcribed) query = query.eq('transcript_status', 'completed');
  if (f.ai_summarized) query = query.eq('summary_status', 'completed');
  if (f.ai_analyzed) query = query.eq('visual_analysis_status', 'completed');
  if (f.created_after) {
    query = query.gte('created_at', `${f.created_after}T00:00:00`);
  }
  if (f.created_before) {
    query = query.lte('created_at', `${f.created_before}T23:59:59.999`);
  }
  if (f.duration_min !== undefined && f.duration_min != null) {
    query = query.gte('duration_seconds', f.duration_min);
  }
  if (f.duration_max !== undefined && f.duration_max != null) {
    query = query.lte('duration_seconds', f.duration_max);
  }
  if (f.aspect_ratios && f.aspect_ratios.length > 0) {
    query = query.in('aspect_bucket', f.aspect_ratios);
  }
  if (f.platforms && f.platforms.length > 0) {
    query = query.in('parsed_media.source_platform', f.platforms);
  }
  const wireValues = mediaTypesToWire(f.media_types);
  if (wireValues !== null) {
    // wireValues is ['__impossible__'] when an active group maps to no
    // parsed_media.media_type (document/other in the web library) — forces
    // zero rows rather than silently returning the unfiltered list.
    query = query.in('parsed_media.media_type', wireValues);
  }
  if (f.has_comments) {
    query = query.gt('parsed_media.comment_count', 0);
  }
  const socialMetricActive =
    (f.min_likes !== undefined && f.min_likes > 0) ||
    (f.min_comments !== undefined && f.min_comments > 0) ||
    (f.min_favorites !== undefined && f.min_favorites > 0) ||
    (f.min_shares !== undefined && f.min_shares > 0);
  if (socialMetricActive) {
    const combine = f.social_combine ?? 'and';
    if (combine === 'and') {
      if (f.min_likes !== undefined && f.min_likes > 0) {
        query = query.gte('parsed_media.like_count', f.min_likes);
      }
      if (f.min_comments !== undefined && f.min_comments > 0) {
        query = query.gte('parsed_media.comment_count', f.min_comments);
      }
      if (f.min_favorites !== undefined && f.min_favorites > 0) {
        query = query.gte('parsed_media.favorite_count', f.min_favorites);
      }
      if (f.min_shares !== undefined && f.min_shares > 0) {
        query = query.gte('parsed_media.share_count', f.min_shares);
      }
    } else {
      const orFragments: string[] = [];
      if (f.min_likes !== undefined && f.min_likes > 0) {
        orFragments.push(`like_count.gte.${f.min_likes}`);
      }
      if (f.min_comments !== undefined && f.min_comments > 0) {
        orFragments.push(`comment_count.gte.${f.min_comments}`);
      }
      if (f.min_favorites !== undefined && f.min_favorites > 0) {
        orFragments.push(`favorite_count.gte.${f.min_favorites}`);
      }
      if (f.min_shares !== undefined && f.min_shares > 0) {
        orFragments.push(`share_count.gte.${f.min_shares}`);
      }
      if (orFragments.length > 0) {
        query = query.or(orFragments.join(','), {
          referencedTable: 'parsed_media',
        });
      }
    }
  }
  return query;
}

/**
 * Fetch video library (loads first LOCAL_CACHE_SIZE for local search)
 */
export const fetchLibrary = async (): Promise<ParsedMedia[]> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.user) {
      throw new Error("User not authenticated");
    }

    const { data, error } = await supabase
      .from('resources')
      .select(RESOURCE_LIST_SELECT)
      .eq('creator_id', session.user.id)
      .eq('source_type', 'web')
      .eq('is_trashed', false)
      .order('created_at', { ascending: false })
      .limit(LOCAL_CACHE_SIZE);

    if (error) throw error;
    return (data || []).map(flattenResourceMedia) as ParsedMedia[];
  } catch (err: any) {
    throw err;
  }
};

/**
 * Fetch total video count (user's resources)
 */
export const fetchLibraryCount = async (): Promise<number> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.user) {
      throw new Error("User not authenticated");
    }

    const { count, error } = await supabase
      .from('resources')
      .select('*', { count: 'exact', head: true })
      .eq('creator_id', session.user.id)
      .eq('source_type', 'web')
      .eq('is_trashed', false);

    if (error) throw error;
    return count || 0;
  } catch (err: any) {
    throw err;
  }
};

export const saveItem = async (item: ParsedMedia): Promise<ParsedMedia> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  // Strip computed/joined fields and dropped columns
  const { tags, summary_text, resource_id, user_id, need_download_video, need_download_music, need_download_cover, ...dbFields } = item as any;
  const payload = {
    ...dbFields,
    published_at: item.published_at || new Date().toISOString(),
    video_download_urls: item.video_download_urls || [],
    image_download_urls: item.image_download_urls || [],
    // DB enum uses lowercase values
    video_download_status: item.video_download_status?.toLowerCase() || 'pending',
    music_download_status: item.music_download_status?.toLowerCase() || 'pending',
    cover_download_status: item.cover_download_status?.toLowerCase() || 'pending',
  };

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .upsert(payload, { onConflict: 'platform_id,source_platform' })
    .select()
    .single();

  if (error) throw error;
  return data as ParsedMedia;
};

export const updateItem = async (id: string, updates: Partial<ParsedMedia>): Promise<ParsedMedia> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  // Strip fields that don't exist on parsed_media table.
  // AI status columns (transcript_status / summary_status /
  // visual_analysis_status) live on `resources`, not `parsed_media`
  // — the workflow writes them via mark_transcript_completed and
  // friends, then Realtime propagates back. Optimistic local-state
  // updates from the MediaCard buttons should NOT try to persist
  // them here (PostgREST returns PGRST204 — column not found —
  // because the schema cache reflects the truth).
  const {
    tags, summary_text, resource_id, user_id,
    transcript_status, summary_status, visual_analysis_status,
    ...dbUpdates
  } = updates as any;

  // Short-circuit: when callers only patch derived/cross-table fields
  // (e.g. MediaCard's optimistic `{transcript_status: 'processing'}`
  // after clicking the Transcript button), `dbUpdates` becomes empty.
  // Sending an empty PATCH to PostgREST + `.single()` would 0-match
  // and raise PGRST116. The actual server-side flip happens in the
  // trigger endpoint (ai_router) → workflow → resources.transcript_status,
  // and propagates back via Realtime. No DB write is needed here.
  if (Object.keys(dbUpdates).length === 0) {
    return updates as ParsedMedia;
  }

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .update(dbUpdates)
    .eq('platform_id', id)
    .select()
    .single();

  if (error) throw error;
  return data as ParsedMedia;
};

export interface DeleteResult {
  success: boolean;
  message: string;
  files_deleted: string[];
}

export const deleteItem = async (id: string, deleteFiles: boolean = false): Promise<DeleteResult> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new Error("User not authenticated");
  }

  const response = await fetch(
    `${getApiUrl()}/api/v1/media/${id}?delete_files=${deleteFiles}`,
    {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${session.access_token}`
      }
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(error.detail || 'Delete failed');
  }

  return await response.json();
};

/**
 * User log interface
 */
export interface UserLog {
  id: string;
  user_id: string;
  action: string;
  message: string;
  status: 'success' | 'error' | 'warning' | 'info' | 'pending';
  platform_id?: string;
  details?: Record<string, unknown>;
  created_at: string;
}

/**
 * Fetch user operation logs
 */
export const fetchUserLogs = async (limit: number = 20): Promise<UserLog[]> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return [];
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      return [];
    }

    const response = await fetch(`${getApiUrl()}/api/v1/media/logs?limit=${limit}`, {
      headers: {
        'Authorization': `Bearer ${session.access_token}`
      }
    });

    if (!response.ok) {
      console.error('Failed to fetch logs:', response.status);
      return [];
    }

    const data = await response.json();
    return data.logs || [];
  } catch (error) {
    console.error('Error fetching logs:', error);
    return [];
  }
};

/**
 * Dashboard statistics interface
 */
export interface DashboardStats {
  totalVideos: number;
  completedDownloads: number;
  pendingDownloads: number;
  failedDownloads: number;
  totalStorageBytes: number;
  uniqueAuthors: number;
  mediaDistribution: { name: string; value: number }[];
  weeklyActivity: { name: string; downloads: number; shares: number }[];
  topTags: { name: string; count: number }[];
  recentLogs: { message: string; time: string; status: 'success' | 'pending' | 'error' }[];
}

/**
 * Fetch dashboard statistics via Supabase RPC (server-side computation)
 */
export const fetchDashboardStats = async (): Promise<DashboardStats> => {
  const supabase = getSupabaseClient();

  // Default empty stats
  const emptyStats: DashboardStats = {
    totalVideos: 0,
    completedDownloads: 0,
    pendingDownloads: 0,
    failedDownloads: 0,
    totalStorageBytes: 0,
    uniqueAuthors: 0,
    mediaDistribution: [
      { name: 'Video', value: 0 },
      { name: 'Images', value: 0 },
      { name: 'Audio', value: 0 },
    ],
    weeklyActivity: [],
    topTags: [],
    recentLogs: [],
  };

  if (!isSupabaseConfigured() || !supabase) {
    return emptyStats;
  }

  try {
    const { data: { claims } } = await supabase.auth.getClaims();
    if (!claims) return emptyStats;

    const { data, error } = await supabase.rpc('get_dashboard_stats', {
      p_user_id: claims.sub,
    });

    if (error) {
      console.error('RPC get_dashboard_stats failed:', error);
      return emptyStats;
    }

    const d = data as Record<string, any>;

    // Map media distribution from RPC format
    const mediaDist = d.media_distribution?.[0] || {};
    const mediaDistribution = [
      { name: 'Video', value: mediaDist.video || 0 },
      { name: 'Images', value: mediaDist.images || 0 },
      { name: 'Audio', value: mediaDist.audio || 0 },
    ];

    // Map weekly activity
    const weeklyActivity = (d.weekly_activity || []).map((w: any) => ({
      name: w.name,
      downloads: w.downloads,
      shares: 0,
    }));

    // Map top tags
    const topTags = (d.top_tags || []).map((t: any) => ({
      name: t.name,
      count: t.count,
    }));

    // Fetch recent logs separately (not in RPC)
    let recentLogs: DashboardStats['recentLogs'] = [];
    try {
      const userLogs = await fetchUserLogs(10);
      if (userLogs.length > 0) {
        recentLogs = userLogs.map(log => ({
          message: log.message,
          time: log.created_at ? new Date(log.created_at).toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
          }) : '',
          status: (log.status === 'success' ? 'success' : log.status === 'error' ? 'error' : 'pending') as 'success' | 'pending' | 'error',
        }));
      }
    } catch (e) {
      console.debug('Failed to fetch user logs:', e);
    }

    return {
      totalVideos: d.total || 0,
      completedDownloads: d.completed || 0,
      pendingDownloads: d.pending || 0,
      failedDownloads: d.failed || 0,
      totalStorageBytes: d.total_storage_bytes || 0,
      uniqueAuthors: d.unique_authors || 0,
      mediaDistribution,
      weeklyActivity,
      topTags,
      recentLogs,
    };
  } catch (e) {
    console.error('Error fetching dashboard stats:', e);
    return emptyStats;
  }
};

/**
 * User settings interface
 */
export interface UserSettingsData {
  id?: string;
  user_id: string;
  download_path: string;
  settings_json?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

/**
 * Fetch user settings
 */
export const fetchUserSettings = async (): Promise<UserSettingsData | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return null;
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      return null;
    }

    const response = await fetch(`${getApiUrl()}/api/v1/settings`, {
      headers: {
        'Authorization': `Bearer ${session.access_token}`
      }
    });

    if (!response.ok) {
      console.error('Failed to fetch settings:', response.status);
      return null;
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching settings:', error);
    return null;
  }
};

/**
 * Save user settings
 */
export const saveUserSettings = async (settings: {
  download_path?: string;
  settings_json?: Record<string, unknown>;
}): Promise<UserSettingsData | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new Error("User not authenticated");
  }

  const response = await fetch(`${getApiUrl()}/api/v1/settings`, {
    method: 'PUT',
    headers: {
      'Authorization': `Bearer ${session.access_token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(settings)
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to save settings' }));
    throw new Error(error.detail || 'Failed to save settings');
  }

  return await response.json();
};
