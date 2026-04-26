import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import { ParsedMedia } from '../types';
import { apiClient } from './apiClient';

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
 * into a flat ParsedMedia object.  Per-user download statuses from
 * the resources table take precedence over parsed_media's global values.
 */
function flattenResourceMedia(row: any): ParsedMedia {
  const pm = row.parsed_media || {};
  return {
    ...pm,
    resource_id: String(row.id),
    video_download_status: row.video_download_status ?? pm.video_download_status,
    music_download_status: row.music_download_status ?? pm.music_download_status,
    cover_download_status: row.cover_download_status ?? pm.cover_download_status,
    image_download_status: row.image_download_status ?? pm.image_download_status,
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
export interface LibraryCursor {
  ts: string;
  id: string;
}

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
  audio: [],
  document: [],
  other: [],
};

/**
 * Resolve a set of tag ids to the intersection of resource ids that
 * carry ALL of them. See resourceService.resolveTagIntersection for
 * the full rationale — this is a local copy to keep dataService
 * self-contained (both live in frontend/services so a single shared
 * helper would be easy but the contexts differ slightly).
 */
async function resolveLibraryTagIntersection(
  supabase: ReturnType<typeof getSupabaseClient>,
  tagIds: string[] | undefined,
): Promise<string[] | null> {
  if (!supabase || !tagIds || tagIds.length === 0) return null;
  let currentIds: Set<string> | null = null;
  for (const tagId of tagIds) {
    let q = supabase.from('resource_tags').select('resource_id').eq('tag_id', tagId);
    if (currentIds !== null) {
      const ids = Array.from(currentIds);
      if (ids.length === 0) return [];
      q = q.in('resource_id', ids);
    }
    const { data, error } = await q;
    if (error) throw error;
    const nextIds = new Set<string>(
      (data ?? []).map((row: { resource_id: string | number }) =>
        String(row.resource_id),
      ),
    );
    if (nextIds.size === 0) return [];
    currentIds = nextIds;
  }
  return currentIds ? Array.from(currentIds) : [];
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

    // Tag intersection first — an empty set short-circuits the paginated
    // query (no rows, no count, no round-trip).
    const tagResourceIds = await resolveLibraryTagIntersection(supabase, f.tag_ids);
    if (tagResourceIds !== null && tagResourceIds.length === 0) {
      return { data: [], totalCount: 0, hasMore: false, page, nextCursor: null };
    }

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
      // Composite keyset: WHERE (created_at, id) < (cursor.ts, cursor.id).
      // PostgREST encoding: or(created_at.lt.<ts>,and(created_at.eq.<ts>,id.lt.<id>)).
      // Without the id tiebreaker, batched inserts that share a single
      // timestamp (e.g. 94 rows at the same crawl tick) get clipped at
      // the page boundary and silently lost — that bug accounted for the
      // 417→340 mismatch users were seeing.
      if (cursor) {
        query = query.or(
          `created_at.lt.${cursor.ts},and(created_at.eq.${cursor.ts},id.lt.${cursor.id})`,
        );
      }
      query = query.limit(pageSize + 1);
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
    const hasMore = rows.length > pageSize;
    const pageData = hasMore ? rows.slice(0, pageSize) : rows;

    // Capture the cursor BEFORE flattenResourceMedia overwrites
    // ``created_at`` (which belongs to resources, not parsed_media).
    const lastRow = pageData[pageData.length - 1] as
      | { id?: string | number; created_at?: string }
      | undefined;
    const nextCursor: LibraryCursor | null =
      hasMore && lastRow && lastRow.created_at && lastRow.id != null
        ? { ts: lastRow.created_at, id: String(lastRow.id) }
        : null;

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
    if (err?.name === 'AbortError') {
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
  if (f.media_types && f.media_types.length > 0) {
    const wireValues = f.media_types.flatMap(
      (t) => MEDIA_TYPE_VALUES_BY_TYPE[t] ?? [],
    );
    if (wireValues.length > 0) {
      query = query.in('parsed_media.media_type', wireValues);
    } else {
      // 'audio' / 'document' / 'other' alone yields zero rows from
      // parsed_media (web library has no such items). Force-empty result
      // by intersecting with an impossible value rather than silently
      // returning the unfiltered list.
      query = query.in('parsed_media.media_type', ['__impossible__']);
    }
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

  // Strip fields that don't exist on parsed_media table
  const { tags, summary_text, resource_id, user_id, ...dbUpdates } = updates as any;

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
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) return emptyStats;

    const { data, error } = await supabase.rpc('get_dashboard_stats', {
      p_user_id: user.id,
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
