import { supabase } from '../supabaseClient';
import { Folder, Resource, ResourceItem, ResourceVersion, SmartCollection } from '../types';
import {
  applyKeysetCursor,
  sliceKeysetPage,
  type KeysetCursor,
  type KeysetListPage,
} from './pagination';
import { getAuthHeaders } from './parserService';
import { apiClient } from './apiClient';
import { getApiUrl } from '../utils/apiConfig';
import { buildMediaUrl } from '../utils/mediaUrl';

// ─── Folders ────────────────────────────────────────────

export async function fetchFolders(
  scopeId: string,
  isPersonal: boolean,
  libraryId?: string | null
): Promise<Folder[]> {
  let query = supabase
    .from('folders')
    .select('*')
    .eq('scope_id', scopeId)
    .eq('is_trashed', false);

  if (libraryId) {
    query = query.eq('library_id', libraryId);
  } else if (!isPersonal) {
    // Team mode without library: show nothing (libraries are the entry point)
    query = query.is('library_id', null);
  }

  const { data, error } = await query.order('sort_order', { ascending: true });

  if (error) throw error;
  return data || [];
}

export async function createFolder(folder: {
  name: string;
  parent_id?: string | null;
  scope_id: string;
}): Promise<Folder> {
  const claims = (await supabase.auth.getClaims()).data.claims;
  if (!claims) throw new Error('Not authenticated');

  // PR-E 4b: scope_type no longer written (nullable post mig 240, dropped 4c).
  const { data, error } = await supabase
    .from('folders')
    .insert({ ...folder, created_by: claims.sub })
    .select()
    .single();

  if (error) throw error;
  return data;
}

export async function renameFolder(id: string, name: string): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({ name })
    .eq('id', id);

  if (error) throw error;
}

export async function renameResource(resourceId: string, filename: string): Promise<void> {
  const { error } = await supabase
    .from('resources')
    .update({ filename })
    .eq('id', resourceId);

  if (error) throw error;
}

export async function updateResource(
  resourceId: string,
  data: { filename?: string; notes?: string; url?: string; rating?: number },
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}`, {
    method: 'PATCH',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update resource');
  const json = await response.json();
  return json.data;
}

export async function trashFolder(id: string): Promise<void> {
  await apiClient.post(`/api/v1/resources/folders/${id}/trash`);
}

export async function getFolderContentCount(
  id: string,
): Promise<{ resource_count: number; subfolder_count: number }> {
  const json = await apiClient.get<{
    data: { resource_count: number; subfolder_count: number };
  }>(`/api/v1/resources/folders/${id}/content-count`);
  return json.data;
}

// Fetch resource_items inside a specific folder (used for trashed folder contents in recycle bin)
export async function fetchFolderContents(
  folderId: string,
  includeTrashed = false,
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)')
    .eq('folder_id', folderId);
  if (!includeTrashed) {
    query = query.eq('resource.is_trashed', false);
  }
  const { data, error } = await query.order('created_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

export async function fetchTrashedFolders(
  scopeId: string,
): Promise<Folder[]> {
  const { data, error } = await supabase
    .from('folders')
    .select('*')
    .eq('scope_id', scopeId)
    .eq('is_trashed', true)
    .order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
}

export async function restoreFolder(id: string): Promise<void> {
  await apiClient.post(`/api/v1/resources/folders/${id}/restore`);
}

// Build folder tree from flat list
export function buildFolderTree(folders: Folder[]): Folder[] {
  const map = new Map<string, Folder>();
  const roots: Folder[] = [];

  folders.forEach(f => map.set(f.id, { ...f, children: [] }));

  map.forEach(folder => {
    if (folder.parent_id && map.has(folder.parent_id)) {
      map.get(folder.parent_id)!.children!.push(folder);
    } else {
      roots.push(folder);
    }
  });

  return roots;
}

// ─── Child Folders (direct children of a parent) ────────

export async function fetchChildFolders(
  scopeId: string,
  isPersonal: boolean,
  parentId: string | null,
  libraryId?: string | null
): Promise<Folder[]> {
  let query = supabase
    .from('folders')
    .select('*')
    .eq('scope_id', scopeId)
    .eq('is_trashed', false);

  if (parentId) {
    query = query.eq('parent_id', parentId);
  } else {
    query = query.is('parent_id', null);
  }

  if (libraryId) {
    query = query.eq('library_id', libraryId);
  } else if (!isPersonal) {
    query = query.is('library_id', null);
  }

  const { data, error } = await query.order('sort_order', { ascending: true });
  if (error) throw error;
  return data || [];
}

// ─── Resource Context (for detail page sibling files) ───

export async function fetchResourceContext(
  resourceId: string
): Promise<{ folder_id: string | null; scope_id: string; library_id: string | null } | null> {
  // PR-E dropped resource_items.scope_type (mig 243). Selecting it caused a
  // 42703 → this returned null → detail-page sibling files silently failed to
  // load. Select only the scope locator columns; the caller derives
  // personal/team from URL context (teamId).
  const { data, error } = await supabase
    .from('resource_items')
    .select('folder_id, scope_id, library_id')
    .eq('resource_id', resourceId)
    .limit(1)
    .single();

  if (error) return null;
  return {
    folder_id: data.folder_id,
    scope_id: data.scope_id,
    library_id: data.library_id,
  };
}

// ─── Resources ──────────────────────────────────────────

/**
 * Parameters accepted by the Resources list endpoint.
 *
 * Scope + folder + library locate the view; the rest come from the
 * filter bar (PR 1-4 chips). All filter params are pushed down into
 * the PostgREST query — they are NOT applied client-side any more.
 *
 * Notes on semantics:
 * - ``tag_ids`` is AND (resource must carry every selected tag).
 *   Implemented as a 2-step query: first resolve the tag set into a
 *   resource_id intersection, then ``.in('resources.id', ids)`` on the
 *   main query. An empty intersection returns [] immediately without
 *   issuing the main query.
 * - ``types`` maps to mime-type prefix matches via ``.or()``. "other"
 *   means "mime-type not starting with any of the known prefixes".
 * - ``platforms`` / ``min_*`` (social) / ``has_comments`` push into the
 *   embedded parsed_media filter via ``.filter(col, op, v,
 *   {referencedTable})``. When any of these is active the join is
 *   upgraded to INNER so resources without a linked parsed_media are
 *   excluded.
 * - ``social_combine='or'`` routes the enabled metric thresholds through
 *   a single ``.or()`` on parsed_media; ``'and'`` applies them as
 *   independent ``.gte()`` filters.
 * - ``aspect_ratios`` uses the generated column ``resources.aspect_bucket``
 *   (migration 143) so a single indexed ``.in()`` does the work.
 */
export interface FetchResourcesParams {
  /** Personal scope when true, team scope when false. Replaces scope_type. */
  isPersonal: boolean;
  scopeId: string;
  folderId?: string | null;
  libraryId?: string | null;
  /** AND-semantic tag id filter. */
  tag_ids?: string[];
  /** Minimum rating (>= filter; 1..5). */
  min_rating?: number;
  /** Broad file-type categories. OR across the set. */
  types?: Array<'video' | 'image' | 'audio' | 'document' | 'other'>;
  /** Source platforms on parsed_media.source_platform. OR across the set. */
  platforms?: string[];
  /** AI status filters — each true means "status == completed". */
  ai_transcribed?: boolean;
  ai_summarized?: boolean;
  ai_analyzed?: boolean;
  /** Created-at inclusive bounds (ISO date YYYY-MM-DD, local calendar). */
  created_after?: string;
  created_before?: string;
  /** Inclusive duration bounds (seconds). Video-specific. */
  duration_min?: number;
  duration_max?: number;
  /** Aspect-ratio bucket wire values ("9:16"/"16:9"/"1:1"/"4:3"/"other"). */
  aspect_ratios?: string[];
  /** Social metric thresholds — inclusive lower bounds on
   *  parsed_media.*_count. */
  min_likes?: number;
  min_comments?: number;
  min_favorites?: number;
  min_shares?: number;
  /** Semantics across enabled metric thresholds. Defaults to 'and'. */
  social_combine?: 'and' | 'or';
  /** AND-on-top floor: parsed_media.comment_count > 0. */
  has_comments?: boolean;
}

/**
 * Known mime-type prefixes. ``other`` is the complement.
 * Mirrors the frontend KNOWN_MIME_PREFIXES list (useResourcesDisplay.ts).
 */
const KNOWN_MIME_PREFIXES = [
  'video/',
  'image/',
  'audio/',
  'application/pdf',
  'application/msword',
  'application/vnd.',
  'text/',
] as const;

type ResourceFilterType = 'video' | 'image' | 'audio' | 'document' | 'other';

/**
 * PostgREST fragments that *positively* match a type. "other" is not
 * expressible as a positive prefix and is handled separately (see
 * ``buildTypeFilterExpression``).
 */
function positiveTypeFragments(type: ResourceFilterType): string[] {
  switch (type) {
    case 'video':
      return ['mime_type.like.video/*'];
    case 'image':
      return ['mime_type.like.image/*'];
    case 'audio':
      return ['mime_type.like.audio/*'];
    case 'document':
      return [
        'mime_type.like.application/pdf*',
        'mime_type.like.application/msword*',
        'mime_type.like.application/vnd.*',
        'mime_type.like.text/*',
      ];
    case 'other':
      return [];
  }
}

/**
 * Compose a PostgREST OR-expression from a set of selected type
 * categories. Returns ``null`` if the set is empty.
 *
 * Examples (PostgREST-shape wire value):
 *   {video}                → mime_type.like.video/*
 *   {video,image}          → or(mime_type.like.video/*,mime_type.like.image/*)
 *   {other}                → and(mime_type.not.like.video/*,mime_type.not.like.image/*,...)
 *   {video,other}          → or(mime_type.like.video/*,and(<nots>))
 *
 * Returned string is what Supabase JS's ``.or(expr, …)`` expects.
 */
function buildTypeFilterExpression(
  types: ResourceFilterType[],
): string | null {
  if (types.length === 0) return null;
  const positiveFrags: string[] = [];
  let includesOther = false;
  for (const t of types) {
    if (t === 'other') {
      includesOther = true;
      continue;
    }
    positiveFrags.push(...positiveTypeFragments(t));
  }

  const fragments: string[] = [...positiveFrags];
  if (includesOther) {
    // AND of NOT-like across every known prefix — rows whose mime_type
    // starts with none of them. Nested ``and(...)`` inside the outer
    // ``or`` is supported by PostgREST.
    const notFragments = KNOWN_MIME_PREFIXES.map(
      (p) => `mime_type.not.like.${p}*`,
    );
    fragments.push(`and(${notFragments.join(',')})`);
  }

  return fragments.length === 1 ? fragments[0] : fragments.join(',');
}

/**
 * Resolve a set of tag ids to the intersection of resource ids that
 * carry ALL of them (AND semantics). Returns:
 *   - null if the filter is inactive (no tag_ids)
 *   - []   if the intersection is empty (caller should short-circuit)
 *   - string[] of resource ids otherwise
 *
 * Runs the tag queries in parallel, chunked to avoid URL-length limits
 * (matches useTagSearchMap / DownloadsView).
 */
async function resolveTagIntersection(
  tagIds: string[] | undefined,
): Promise<string[] | null> {
  if (!tagIds || tagIds.length === 0) return null;
  // First query: resource_ids carrying the first tag. Subsequent tags
  // only need to match that set (and then the set shrinks each step).
  let currentIds: Set<string> | null = null;
  for (const tagId of tagIds) {
    let q = supabase.from('resource_tags').select('resource_id').eq('tag_id', tagId);
    if (currentIds !== null) {
      // Narrow to the running candidate set — reduces payload and
      // avoids retrieving rows we'd filter out in JS anyway.
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

export async function fetchResources(
  isPersonalOrParams: boolean | FetchResourcesParams,
  scopeId?: string,
  folderId?: string | null,
  libraryId?: string | null,
): Promise<ResourceItem[]> {
  // Backwards-compatible overload: existing callers still pass positional
  // arguments. New callers should pass a FetchResourcesParams object.
  const params: FetchResourcesParams =
    typeof isPersonalOrParams === 'object'
      ? isPersonalOrParams
      : {
          isPersonal: isPersonalOrParams,
          scopeId: scopeId as string,
          folderId,
          libraryId,
        };

  // ── Tags: AND semantics via pre-resolved resource_id intersection. ──
  const tagResourceIds = await resolveTagIntersection(params.tag_ids);
  if (tagResourceIds !== null && tagResourceIds.length === 0) {
    // No resource carries every tag → empty result, skip main query.
    return [];
  }

  const query = buildResourceItemsQuery(params, tagResourceIds);
  const { data, error } = await query.order('created_at', { ascending: false });
  if (error) throw error;
  // The query builder's return type narrows as we chain filters; cast back.
  return (data as unknown as ResourceItem[]) ?? [];
}

/**
 * Build the filtered resource_items query (scope / folder / library / tag /
 * rating / AI status / date / duration / aspect / type / platform / social),
 * WITHOUT order or execute — so the bulk `fetchResources` and the paginated
 * `fetchResourcesPaginated` share ONE filter implementation. The caller resolves
 * the tag intersection first (an empty set short-circuits before reaching here).
 */
function buildResourceItemsQuery(
  params: FetchResourcesParams,
  tagResourceIds: string[] | null,
  opts: { count?: boolean } = {},
) {
  // Social / platform / has_comments filters hit parsed_media columns,
  // which means the nested join needs to be INNER (so a resource without
  // a parsed_media row is excluded). Otherwise keep the outer LEFT join
  // so uploaded resources still show up.
  const socialMetricActive =
    (params.min_likes !== undefined && params.min_likes > 0) ||
    (params.min_comments !== undefined && params.min_comments > 0) ||
    (params.min_favorites !== undefined && params.min_favorites > 0) ||
    (params.min_shares !== undefined && params.min_shares > 0);
  const needsMediaInner =
    (params.platforms && params.platforms.length > 0) ||
    socialMetricActive ||
    Boolean(params.has_comments);
  const mediaJoin = needsMediaInner ? 'parsed_media!inner' : 'parsed_media';
  const selectExpr = `*, resource:resources!inner(*, media:${mediaJoin}(id, source_platform, like_count, comment_count, favorite_count, share_count))`;

  let query = supabase
    .from('resource_items')
    .select(selectExpr, opts.count ? { count: 'exact', head: true } : undefined)
    .eq('scope_id', params.scopeId)
    .eq('resources.is_trashed', false)
    .neq('resource.source_type', 'web');

  if (params.folderId) {
    query = query.eq('folder_id', params.folderId);
  } else {
    query = query.is('folder_id', null);
  }

  if (params.libraryId) {
    query = query.eq('library_id', params.libraryId);
  } else if (!params.isPersonal) {
    query = query.is('library_id', null);
  }

  // ── Apply tag intersection (if any) on the resources embed. ──
  if (tagResourceIds !== null) {
    query = query.in('resources.id', tagResourceIds);
  }

  // ── Rating (resources.rating >= min). ──
  if (params.min_rating !== undefined && params.min_rating > 0) {
    query = query.gte('resources.rating', params.min_rating);
  }

  // ── AI status (resources.{transcript,summary,visual_analysis}_status). ──
  if (params.ai_transcribed) {
    query = query.eq('resources.transcript_status', 'completed');
  }
  if (params.ai_summarized) {
    query = query.eq('resources.summary_status', 'completed');
  }
  if (params.ai_analyzed) {
    query = query.eq('resources.visual_analysis_status', 'completed');
  }

  // ── Created-at window (resources.created_at; inclusive day bounds). ──
  if (params.created_after) {
    query = query.gte('resources.created_at', `${params.created_after}T00:00:00`);
  }
  if (params.created_before) {
    query = query.lte(
      'resources.created_at',
      `${params.created_before}T23:59:59.999`,
    );
  }

  // ── Duration (resources.duration_seconds). ──
  if (params.duration_min !== undefined && params.duration_min != null) {
    query = query.gte('resources.duration_seconds', params.duration_min);
  }
  if (params.duration_max !== undefined && params.duration_max != null) {
    query = query.lte('resources.duration_seconds', params.duration_max);
  }

  // ── Aspect bucket (generated column, migration 143). ──
  if (params.aspect_ratios && params.aspect_ratios.length > 0) {
    query = query.in('resources.aspect_bucket', params.aspect_ratios);
  }

  // ── Type (mime-type prefix match via .or() on the resources embed). ──
  if (params.types && params.types.length > 0) {
    const expr = buildTypeFilterExpression(params.types);
    if (expr) {
      query = query.or(expr, { referencedTable: 'resources' });
    }
  }

  // ── Source platforms (parsed_media.source_platform IN ...). ──
  if (params.platforms && params.platforms.length > 0) {
    query = query.in('resources.parsed_media.source_platform', params.platforms);
  }

  // ── has_comments: parsed_media.comment_count > 0. AND-on-top floor. ──
  if (params.has_comments) {
    query = query.gt('resources.parsed_media.comment_count', 0);
  }

  // ── Social metric thresholds. AND = independent .gte(), OR = .or(). ──
  if (socialMetricActive) {
    const combine = params.social_combine ?? 'and';
    if (combine === 'and') {
      if (params.min_likes !== undefined && params.min_likes > 0) {
        query = query.gte('resources.parsed_media.like_count', params.min_likes);
      }
      if (params.min_comments !== undefined && params.min_comments > 0) {
        query = query.gte(
          'resources.parsed_media.comment_count',
          params.min_comments,
        );
      }
      if (params.min_favorites !== undefined && params.min_favorites > 0) {
        query = query.gte(
          'resources.parsed_media.favorite_count',
          params.min_favorites,
        );
      }
      if (params.min_shares !== undefined && params.min_shares > 0) {
        query = query.gte('resources.parsed_media.share_count', params.min_shares);
      }
    } else {
      const orFragments: string[] = [];
      if (params.min_likes !== undefined && params.min_likes > 0) {
        orFragments.push(`like_count.gte.${params.min_likes}`);
      }
      if (params.min_comments !== undefined && params.min_comments > 0) {
        orFragments.push(`comment_count.gte.${params.min_comments}`);
      }
      if (params.min_favorites !== undefined && params.min_favorites > 0) {
        orFragments.push(`favorite_count.gte.${params.min_favorites}`);
      }
      if (params.min_shares !== undefined && params.min_shares > 0) {
        orFragments.push(`share_count.gte.${params.min_shares}`);
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
 * Keyset-paginated variant of `fetchResources` — the scale-safe path for the
 * Resources library / folders / scopes (which can exceed PostgREST's 1000-row
 * cap once the Eagle import lands). Shares every filter with `fetchResources`
 * via `buildResourceItemsQuery`; only the ordering, the keyset cursor, and the
 * first-page count differ. Pass `cursor=null` for the first page, then forward
 * `result.nextCursor`.
 */
export async function fetchResourcesPaginated(
  params: FetchResourcesParams,
  cursor: KeysetCursor | null,
  pageSize: number,
  signal?: AbortSignal,
): Promise<KeysetListPage<ResourceItem>> {
  // Tag-filtered → run the whole filtered + keyset query server-side via the
  // search_scope_resources RPC (mig 269). The old path resolved tags to a
  // resource_id list and passed it back as `.in('resources.id', [...])` — that
  // list silently capped at PostgREST's 1000-row ceiling AND the giant `.in()`
  // URL risked a Kong/nginx 502 at scale. The no-tag path below stays on the
  // PostgREST keyset query (already scale-safe).
  if (params.tag_ids && params.tag_ids.length > 0) {
    return fetchResourcesViaRpc(params, cursor, pageSize, signal);
  }

  // Order (created_at DESC, id DESC) so the id tiebreaker keeps batch-inserted
  // rows (same created_at) from being clipped at a page boundary.
  let q = buildResourceItemsQuery(params, null)
    .order('created_at', { ascending: false })
    .order('id', { ascending: false });
  q = applyKeysetCursor(q, cursor, pageSize);
  if (signal) q = q.abortSignal(signal);

  const { data, error } = await q;
  if (error) throw error;

  const rows = (data as unknown as ResourceItem[]) ?? [];
  const page = sliceKeysetPage(rows, pageSize, (row) => {
    const r = row as { id?: string | number; created_at?: string };
    return r.created_at && r.id != null
      ? { ts: r.created_at, id: String(r.id) }
      : null;
  });

  // Fast total count only on the first page (cursor === null), reusing the
  // exact filter set so the count matches what the user is paging through.
  let totalCount = -1;
  if (cursor === null) {
    const { count } = await buildResourceItemsQuery(params, null, {
      count: true,
    });
    totalCount = count ?? -1;
  }

  return { ...page, totalCount };
}

/**
 * Server-side filtered + keyset-paginated resource search (mig 269 RPC). Used
 * for the tag-filtered case so tag-AND scales past PostgREST's 1000-row cap.
 * Returns the same shape as fetchResourcesPaginated. bigIntSafeFetch (the
 * client's global fetch) keeps Snowflake ids precision-safe in the jsonb rows.
 */
async function fetchResourcesViaRpc(
  params: FetchResourcesParams,
  cursor: KeysetCursor | null,
  pageSize: number,
  signal?: AbortSignal,
): Promise<KeysetListPage<ResourceItem>> {
  let req = supabase.rpc('search_scope_resources', {
    p_scope_id: params.scopeId,
    p_is_personal: params.isPersonal,
    p_folder_id: params.folderId ?? null,
    p_library_id: params.libraryId ?? null,
    p_tag_ids: params.tag_ids ?? null,
    p_min_rating: params.min_rating ?? null,
    p_ai_transcribed: params.ai_transcribed ?? null,
    p_ai_summarized: params.ai_summarized ?? null,
    p_ai_analyzed: params.ai_analyzed ?? null,
    p_created_after: params.created_after ?? null,
    p_created_before: params.created_before ?? null,
    p_duration_min: params.duration_min ?? null,
    p_duration_max: params.duration_max ?? null,
    p_aspect_ratios: params.aspect_ratios ?? null,
    p_types: params.types ?? null,
    p_platforms: params.platforms ?? null,
    p_has_comments: params.has_comments ?? null,
    p_min_likes: params.min_likes ?? null,
    p_min_comments: params.min_comments ?? null,
    p_min_favorites: params.min_favorites ?? null,
    p_min_shares: params.min_shares ?? null,
    p_social_combine: params.social_combine ?? 'and',
    p_cursor_ts: cursor?.ts ?? null,
    p_cursor_id: cursor?.id ?? null,
    p_limit: pageSize + 1,
    p_with_count: cursor === null,
  });
  if (signal) req = req.abortSignal(signal);

  const { data, error } = await req;
  if (error) throw error;

  const result = (data ?? { rows: [], total_count: null }) as {
    rows: ResourceItem[];
    total_count: number | null;
  };
  const rows = result.rows ?? [];
  const page = sliceKeysetPage(rows, pageSize, (row) => {
    const r = row as { id?: string | number; created_at?: string };
    return r.created_at && r.id != null
      ? { ts: r.created_at, id: String(r.id) }
      : null;
  });
  return { ...page, totalCount: result.total_count ?? -1 };
}

export async function fetchResourceCount(
  isPersonal: boolean,
  scopeId: string
): Promise<number> {
  // Count rows in `resources` (not the resource_items junction) so a resource
  // that appears in multiple scope buckets isn't double-counted. Mirrors the
  // filters applied by the My Uploads view itself so the sidebar number
  // matches what the user sees in the main area.
  const { data: { session } } = await supabase.auth.getSession();
  const userId = session?.user?.id;
  if (!userId) return 0;

  let query = supabase
    .from('resources')
    .select('id', { count: 'exact', head: true })
    .eq('creator_id', userId)
    .eq('is_trashed', false)
    .neq('source_type', 'web');

  // Personal scope is implicit (creator_id already filters to current
  // user). For team scope, resources need to be explicitly linked to the
  // team via resource_items — fall back to the old junction query in that
  // case since resources themselves don't carry a team_id column.
  // Team scope: resources link to the team via resource_items, and an upload
  // can sit in several folders → COUNT(DISTINCT) in SQL (RPC, mig 268). Replaces
  // the old id-list (silently capped at 1000 + a 502-risk giant `.in(...)` URL).
  if (!isPersonal) {
    const { data, error } = await supabase.rpc('count_scope_resources', {
      p_scope_id: scopeId,
      p_web: false,
    });
    if (error) throw error;
    return (data as number) ?? 0;
  }

  const { count, error } = await query;
  if (error) throw error;
  return count || 0;
}

// ─── Duplicate Detection ─────────────────────────────────

export async function checkDuplicate(
  fileHash: string,
  fileSize: number,
): Promise<{ duplicate: boolean; existing: Resource | null }> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    file_hash: fileHash,
    file_size: String(fileSize),
  });
  try {
    const response = await fetch(`${apiUrl}/api/v1/resources/check-duplicate?${params}`, {
      headers: await getAuthHeaders(),
    });
    if (!response.ok) return { duplicate: false, existing: null };
    return response.json();
  } catch {
    return { duplicate: false, existing: null };
  }
}

export async function linkExistingResource(
  resourceId: string,
  scopeId: string,
  folderId?: string | null,
  libraryId?: string | null,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    resource_id: resourceId,
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);
  if (libraryId) params.set('library_id', libraryId);

  const response = await fetch(`${apiUrl}/api/v1/resources/link-existing?${params}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to link resource');
  const json = await response.json();
  return json.data;
}

// ─── Upload ──────────────────────────────────────────────

export async function uploadResource(
  file: File,
  scopeId: string,
  folderId?: string | null,
  onProgress?: (progress: number) => void,
  libraryId?: string | null,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const formData = new FormData();
  formData.append('file', file);

  // Build headers without Content-Type (browser sets multipart boundary)
  const headers: Record<string, string> = {};
  const authHeaders = await getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const params = new URLSearchParams({
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);
  if (libraryId) params.set('library_id', libraryId);

  // Use XMLHttpRequest for progress tracking
  if (onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const json = JSON.parse(xhr.responseText);
          resolve(json.data);
        } else {
          reject(new Error('Failed to upload resource'));
        }
      });
      xhr.addEventListener('error', () => reject(new Error('Upload failed')));
      xhr.open('POST', `${apiUrl}/api/v1/resources/upload?${params}`);
      Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
      xhr.send(formData);
    });
  }

  const response = await fetch(`${apiUrl}/api/v1/resources/upload?${params}`, {
    method: 'POST',
    headers,
    body: formData,
  });
  if (!response.ok) throw new Error('Failed to upload resource');
  const json = await response.json();
  return json.data;
}

// ─── Cover / Lyrics ──────────────────────────────────────

/**
 * Build headers for multipart uploads: reuse auth headers but drop any
 * Content-Type key so the browser sets the multipart boundary itself.
 */
async function multipartHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
  const auth = await getAuthHeaders();
  Object.entries(auth).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });
  return headers;
}

export async function uploadResourceCover(
  resourceId: string,
  file: File,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/cover`, {
    method: 'POST',
    headers: await multipartHeaders(),
    body: fd,
  });
  if (!res.ok) throw new Error('Failed to upload cover');
  return (await res.json()).data;
}

export async function uploadResourceLyrics(
  resourceId: string,
  file: File,
): Promise<{
  lrc: string;
  lines: Array<{ text: string; line_start_ms: number | null }>;
}> {
  const apiUrl = getApiUrl();
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/lyrics`, {
    method: 'POST',
    headers: await multipartHeaders(),
    body: fd,
  });
  if (!res.ok) {
    const e = await res
      .json()
      .catch(() => ({ detail: 'Failed to upload lyrics' }));
    throw new Error(e.detail || `HTTP ${res.status}`);
  }
  return (await res.json()).data.lyrics_json;
}

export async function getResourceLyrics(
  resourceId: string,
): Promise<{
  lrc: string;
  lines: Array<{ text: string; line_start_ms: number | null }>;
} | null> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/lyrics`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) return null;
  return (await res.json()).data ?? null;
}

// ─── Trash / Restore ─────────────────────────────────────

export async function trashResource(
  resourceId: string,
  scopeId: string,
  folderId?: string | null,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);

  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}?${params}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to trash resource');
}

export async function restoreResource(resourceId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/restore`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to restore resource');
}

export async function permanentDeleteResource(resourceId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/permanent`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to permanently delete resource');
}

export async function permanentDeleteFolder(folderId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/folders/${folderId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to permanently delete folder');
}

// ─── Trashed resources ───────────────────────────────────

export async function fetchTrashedResources(
  isPersonal: boolean,
  scopeId: string
): Promise<ResourceItem[]> {
  // Query resources where is_trashed=true.
  //
  // On trash the resource_items row is deleted and the origin is snapshotted
  // onto the resource as last_scope_type / last_scope_id (the restore-location
  // snapshot — it survives PR-E, it is NOT one of the dropped scope_type
  // columns). So the recycle bin must filter by that snapshot.
  //
  // Post PR-C/PR-E, `scopeId` is the team snowflake — for personal mode it is
  // the *personal-team* id, NOT the user UUID. trash_resource() writes
  // last_scope_id = personal-team snowflake + last_scope_type = 'personal',
  // so personal must filter by last_scope (mirroring team). The old
  // `creator_id == scopeId` check compared a UUID column to a bigint id and
  // always returned zero rows → empty recycle bin.
  const query = supabase
    .from('resources')
    .select('*')
    .eq('is_trashed', true)
    .eq('last_scope_type', isPersonal ? 'personal' : 'team')
    .eq('last_scope_id', scopeId);

  const { data, error } = await query.order('trashed_at', { ascending: false });

  if (error) throw error;

  // Wrap each resource in a ResourceItem-like shape for compatibility
  return (data || []).map((resource): ResourceItem => ({
    id: resource.id,
    resource_id: resource.id,
    scope_id: scopeId,
    folder_id: resource.last_folder_id ?? null,
    library_id: resource.last_library_id ?? null,
    added_by: resource.created_by ?? null,
    created_at: resource.created_at,
    resource,
  }));
}

// ─── Single Resource ────────────────────────────────────

export async function fetchResourceById(resourceId: string): Promise<Resource> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch resource');
  const json = await response.json();
  return json.data;
}

// ─── Versions ───────────────────────────────────────────

export async function fetchResourceVersions(resourceId: string): Promise<ResourceVersion[]> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/versions`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch versions');
  const json = await response.json();
  return json.data || [];
}

// ─── Version Management ─────────────────────────────────

export async function uploadNewVersion(
  resourceId: string,
  file: File,
  notes?: string,
): Promise<ResourceVersion> {
  const apiUrl = getApiUrl();
  const formData = new FormData();
  formData.append('file', file);

  const headers: Record<string, string> = {};
  const authHeaders = await getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const params = new URLSearchParams();
  if (notes) params.set('notes', notes);

  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions?${params}`,
    { method: 'POST', headers, body: formData },
  );
  if (!response.ok) throw new Error('Failed to upload new version');
  const json = await response.json();
  return json.data;
}

export async function setCurrentVersion(
  resourceId: string,
  versionNumber: number,
): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionNumber}/set-current`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!response.ok) throw new Error('Failed to set current version');
}

export async function deleteVersion(
  resourceId: string,
  versionId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}`,
    { method: 'DELETE', headers: await getAuthHeaders() },
  );
  if (!response.ok) throw new Error('Failed to delete version');
}

// ─── File URL ───────────────────────────────────────────

/**
 * Build a /media/{id} URL for a resource.
 * Backend resolves the file path from DB — the URL never exposes filenames.
 */
export function getResourceMediaUrl(resourceId: string, token?: string): string {
  return buildMediaUrl(resourceId, token);
}

export function getResourceFileUrl(resourceId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/file`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export function getResourceCoverUrl(
  resourceId: string,
  token?: string,
  version?: string | number,
): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/cover`;
  const params = new URLSearchParams();
  if (token) {
    params.set('token', token);
  }
  if (version !== undefined && version !== null && `${version}` !== '') {
    // Cache-bust: the cover route serves an immutable, long-max-age response,
    // so a re-uploaded cover would otherwise show the stale image.
    params.set('v', `${version}`);
  }
  const query = params.toString();
  return query ? `${base}?${query}` : base;
}

export function getVersionFileUrl(resourceId: string, versionId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/file`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export function getVersionHlsUrl(resourceId: string, versionId: string, token?: string): string {
  const apiUrl = getApiUrl();
  const base = `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/hls/master.m3u8`;
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export async function retryTranscode(resourceId: string, versionId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/transcode`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to queue transcode');
  }
}

export function getPreviewSpriteUrl(resourceId: string): string {
  const apiUrl = getApiUrl();
  return `${apiUrl}/api/v1/resources/${resourceId}/preview-sprite`;
}

// ─── Tags ────────────────────────────────────────────────

export async function fetchResourceTags(resourceId: string) {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch resource tags');
  const json = await response.json();
  return json.data || [];
}

export async function addResourceTag(resourceId: string, tagId: string) {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ tag_id: tagId }),
  });
  if (!response.ok) throw new Error('Failed to add tag');
  const json = await response.json();
  return json.data;
}

export async function removeResourceTag(resourceId: string, tagId: string) {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/tags/${tagId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to remove tag');
}

// ─── Downloaded Resources (from Parser) ─────────────

export async function fetchDownloadedResources(
  scopeId: string,
): Promise<ResourceItem[]> {
  const { data, error } = await supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)')
    .eq('scope_id', scopeId)
    .eq('resource.source_type', 'web')
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

export async function fetchDownloadedResourceCount(
  isPersonal: boolean,
  scopeId: string,
): Promise<number> {
  // Count `resources` (not the resource_items junction) to match the
  // DownloadsView page query exactly. Filters: creator_id = current user,
  // source_type = 'web', is_trashed = false. That's the same set
  // fetchLibraryPaginated returns, so sidebar and main-area counts agree.
  const { data: { session } } = await supabase.auth.getSession();
  const userId = session?.user?.id;
  if (!userId) return 0;

  let query = supabase
    .from('resources')
    .select('id', { count: 'exact', head: true })
    .eq('creator_id', userId)
    .eq('source_type', 'web')
    .eq('is_trashed', false);

  // Team scope: COUNT(DISTINCT) web-sourced resources in the scope via SQL (RPC,
  // mig 268) — scale-safe, replaces the 1000-capped id-list.
  if (!isPersonal) {
    const { data, error } = await supabase.rpc('count_scope_resources', {
      p_scope_id: scopeId,
      p_web: true,
    });
    if (error) throw error;
    return (data as number) ?? 0;
  }

  const { count, error } = await query;
  if (error) throw error;
  return count || 0;
}

// ─── Smart Folders ───────────────────────────────────────

export interface SmartFolderCondition {
  field: string;
  op: string;
  value: string;
}

export interface SmartFolderRules {
  operator: 'AND' | 'OR';
  match: boolean;
  conditions: SmartFolderCondition[];
}

export async function fetchSmartFolders(
  scopeId: string
): Promise<SmartCollection[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_id: scopeId });
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch smart folders');
  const json = await response.json();
  return json.data || [];
}

export async function createSmartFolder(
  name: string,
  scopeId: string,
  rules: SmartFolderRules,
): Promise<SmartCollection> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ name, scope_id: scopeId, rules }),
  });
  if (!response.ok) throw new Error('Failed to create smart folder');
  const json = await response.json();
  return json.data;
}

export async function updateSmartFolder(
  folderId: string,
  update: { name?: string; rules?: SmartFolderRules },
): Promise<SmartCollection> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders/${folderId}`, {
    method: 'PATCH',
    headers: await getAuthHeaders(),
    body: JSON.stringify(update),
  });
  if (!response.ok) throw new Error('Failed to update smart folder');
  const json = await response.json();
  return json.data;
}

export async function deleteSmartFolder(folderId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders/${folderId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete smart folder');
}

export async function fetchSmartFolderResults(
  folderId: string,
  scopeId: string,
): Promise<ResourceItem[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_id: scopeId });
  const response = await fetch(`${apiUrl}/api/v1/resources/smart-folders/${folderId}/results?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch smart folder results');
  const json = await response.json();
  return json.data || [];
}

/**
 * Query resources matching a smart folder's rules (client-side fallback).
 */
export async function fetchSmartFolderResources(
  scopeId: string,
  rules: { match: string; conditions: Array<{ field: string; operator: string; value: string }> }
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources!inner(*)')
    .eq('scope_id', scopeId)
    .eq('resources.is_trashed', false);

  for (const cond of (rules.conditions || [])) {
    const col = `resource.${cond.field}`;
    switch (cond.operator) {
      case 'equals':
        query = query.eq(col, cond.value);
        break;
      case 'contains':
        query = query.ilike(col, `%${cond.value}%`);
        break;
      case 'starts_with':
        query = query.ilike(col, `${cond.value}%`);
        break;
      case 'greater_than':
        query = query.gt(col, cond.value);
        break;
      case 'less_than':
        query = query.lt(col, cond.value);
        break;
    }
  }

  const { data, error } = await query.order('created_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

// ─── Move / Copy / Batch ─────────────────────────────

// 移动文件到指定文件夹
export async function moveResourceItem(
  resourceItemId: string,
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const update: Record<string, any> = { folder_id: targetFolderId };
  if (targetLibraryId !== undefined) update.library_id = targetLibraryId;
  const { data, error } = await supabase
    .from('resource_items')
    .update(update)
    .eq('id', resourceItemId)
    .select('id');
  if (error) throw error;
  if (!data || data.length === 0) {
    throw new Error('Move failed: item not found or permission denied');
  }
}

// 批量移动
export async function moveResourceItems(
  resourceItemIds: string[],
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const update: Record<string, any> = { folder_id: targetFolderId };
  if (targetLibraryId !== undefined) update.library_id = targetLibraryId;
  const { data, error } = await supabase
    .from('resource_items')
    .update(update)
    .in('id', resourceItemIds)
    .select('id');
  if (error) throw error;
  if (!data || data.length === 0) {
    throw new Error('Move failed: items not found or permission denied');
  }
}

// 复制文件（创建新 resource_item 指向同一个 resource）
export async function copyResourceItem(
  resourceId: string,
  targetScopeId: string,
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<ResourceItem> {
  const claims = (await supabase.auth.getClaims()).data.claims;
  if (!claims) throw new Error('Not authenticated');
  // PR-E 4b: scope_type no longer written (nullable post mig 240, dropped 4c).
  const { data, error } = await supabase
    .from('resource_items')
    .insert({
      resource_id: resourceId,
      scope_id: targetScopeId,
      folder_id: targetFolderId,
      library_id: targetLibraryId || null,
      added_by: claims.sub,
    })
    .select('*, resource:resources(*)')
    .single();
  if (error) throw error;
  return data;
}

// 移动文件夹（级联更新子文件夹和 resource_items 的 library_id）
export async function moveFolder(
  folderId: string,
  targetParentId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const update: Record<string, any> = { parent_id: targetParentId };
  if (targetLibraryId !== undefined) update.library_id = targetLibraryId;
  const { error } = await supabase
    .from('folders')
    .update(update)
    .eq('id', folderId);
  if (error) throw error;

  // Cascade library_id to sub-folders and resource_items
  if (targetLibraryId !== undefined) {
    // BFS to collect all descendant folder IDs
    const allFolderIds = [folderId];
    let queue = [folderId];
    while (queue.length > 0) {
      const { data: children, error: childErr } = await supabase
        .from('folders')
        .select('id')
        .in('parent_id', queue);
      if (childErr) throw childErr;
      if (!children || children.length === 0) break;
      const childIds = children.map((c: any) => String(c.id));
      allFolderIds.push(...childIds);
      queue = childIds;
    }

    // Update sub-folders' library_id
    if (allFolderIds.length > 1) {
      const { error: folderErr } = await supabase
        .from('folders')
        .update({ library_id: targetLibraryId })
        .in('id', allFolderIds.slice(1));
      if (folderErr) throw folderErr;
    }

    // Update all resource_items in affected folders
    const { error: itemErr } = await supabase
      .from('resource_items')
      .update({ library_id: targetLibraryId })
      .in('folder_id', allFolderIds);
    if (itemErr) throw itemErr;
  }
}

// ─── Folder Preview ──────────────────────────────────

export async function getFolderPreview(
  folderId: string
): Promise<Array<{ resource_id: string | null; thumbnail_path: string | null; cover_image_path: string | null; mime_type: string | null }>> {
  const { data, error } = await supabase
    .from('resource_items')
    .select('resource:resources!inner(id, thumbnail_path, cover_image_path, mime_type)')
    .eq('folder_id', folderId)
    .eq('resource.is_trashed', false)
    .neq('resource.source_type', 'web')
    .limit(4);
  if (error) throw error;
  return (data || []).map((item: any) => ({
    resource_id: item.resource?.id ? String(item.resource.id) : null,
    thumbnail_path: item.resource?.thumbnail_path ?? null,
    cover_image_path: item.resource?.cover_image_path ?? null,
    mime_type: item.resource?.mime_type ?? null,
  }));
}

// Soft-delete a resource by parsed_media.id (used by PlayerPage which has media id directly).
// - Personal scope (default): sets is_trashed=true on the resource (global trash).
// - Team scope: only unlinks from the team library, keeps the resource in personal library.
export async function trashResourceByMediaId(
  mediaId: string,
  scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams();
  if (scopeId) params.set('scope_id', scopeId);
  const query = params.toString() ? `?${params}` : '';
  const response = await fetch(
    `${apiUrl}/api/v1/resources/by-media-id/${mediaId}/trash${query}`,
    {
      method: 'POST',
      headers: await getAuthHeaders(),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Trash failed' }));
    throw new Error(err.detail || 'Failed to trash resource');
  }
}

// Soft-delete a downloaded video by moving it to the recycle bin (by platform_id).
// - Personal scope (default): sets is_trashed=true on the resource (global trash).
// - Team scope: only unlinks from the team library, keeps the resource in personal library.
export async function trashResourceByPlatformId(
  platformId: string,
  scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams();
  if (scopeId) params.set('scope_id', scopeId);
  const query = params.toString() ? `?${params}` : '';
  const response = await fetch(
    `${apiUrl}/api/v1/resources/by-platform-id/${platformId}/trash${query}`,
    {
      method: 'POST',
      headers: await getAuthHeaders(),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Trash failed' }));
    throw new Error(err.detail || 'Failed to trash resource');
  }
}

// Unlink a downloaded video's resource from the user's scope (by media_id).
// Does NOT delete the parsed_media record or physical files.
// The DB orphan-GC trigger auto-trashes the resource if no references remain.
export async function unlinkResourceByPlatformId(
  platformId: string,
  scopeId?: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams();
  if (scopeId) params.set('scope_id', scopeId);
  const response = await fetch(
    `${apiUrl}/api/v1/resources/by-platform-id/${platformId}?${params}`,
    {
      method: 'DELETE',
      headers: await getAuthHeaders(),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Unlink failed' }));
    throw new Error(err.detail || 'Failed to unlink resource');
  }
}

// 批量删除
export async function trashResources(
  resourceIds: string[],
  scopeId: string,
  folderId?: string | null,
): Promise<void> {
  const apiUrl = getApiUrl();
  const headers = await getAuthHeaders();
  await Promise.all(resourceIds.map(async (id) => {
    const params = new URLSearchParams({
      scope_id: scopeId,
    });
    if (folderId) params.set('folder_id', folderId);

    const response = await fetch(`${apiUrl}/api/v1/resources/${id}?${params}`, {
      method: 'DELETE',
      headers,
    });
    if (!response.ok) throw new Error('Failed to trash resource');
  }));
}
