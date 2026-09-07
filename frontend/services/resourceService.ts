import { supabase } from '../supabaseClient';
import { fetchAllRows } from '../utils/pgAllRows';
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
import { chunked, PG_IN_CHUNK } from '../utils/chunk';

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
  data: {
    filename?: string;
    notes?: string;
    gen_prompt?: string;
    gen_prompt_zh?: string;
    slide_prompts?: Resource['slide_prompts'];
    url?: string;
    rating?: number;
  },
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

/** Reverse-engineer a bilingual generation prompt from the image via the
 *  user's assigned caption agent. Async — returns the task id; the
 *  workflow writes gen_prompt / gen_prompt_zh when it finishes. */
export async function generateGenPrompt(resourceId: string): Promise<string> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/gen-prompt/generate`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!response.ok) {
    const detail = await response
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(detail || 'Failed to start prompt generation');
  }
  const json = await response.json();
  return json.task_id;
}

/** Reverse-engineer the prompt for ONE slide of a downloaded album.
 *  Async — returns the task id; the workflow merges the result into
 *  `resources.slide_prompts[slideName]` (other slides untouched) when it
 *  finishes, so the caller should refetch that map on completion. */
export async function generateSlidePrompt(
  resourceId: string,
  slideName: string,
): Promise<string> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/slides/${encodeURIComponent(slideName)}/generate-prompt`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!response.ok) {
    const detail = await response
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(detail || 'Failed to start slide prompt generation');
  }
  const json = await response.json();
  return json.task_id;
}

/** 12-dimension bilingual auto-tagging via the user's assigned classify
 *  agent. Async — returns the task id; the workflow attaches
 *  resource_tags (source='ai') when it finishes. */
export async function classifyResource(resourceId: string): Promise<string> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/classify`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!response.ok) {
    const detail = await response
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(detail || 'Failed to start auto-tagging');
  }
  const json = await response.json();
  return json.task_id;
}

/** Batch-dispatch caption/classify workflows for up to 50 image
 *  resources. Each image gets its own Task Center row; non-image /
 *  inaccessible entries come back in `skipped` with a reason. */
export async function batchAssetAi(
  resourceIds: string[],
  operation: 'caption' | 'classify',
): Promise<{
  dispatched: Array<{ resource_id: string; task_id: string }>;
  skipped: Array<{ resource_id: string; reason: string }>;
}> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/ai/batch`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ resource_ids: resourceIds, operation }),
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(
      typeof detail === 'string' ? detail : 'Failed to dispatch batch AI tasks',
    );
  }
  const json = await response.json();
  return { dispatched: json.dispatched ?? [], skipped: json.skipped ?? [] };
}

/** Download a LoRA-training zip (image + same-stem .txt caption per
 *  asset) for up to 100 image resources. Triggers a browser download. */
export async function exportTrainingSet(
  resourceIds: string[],
  lang: 'en' | 'zh',
): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/export/training-set`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ resource_ids: resourceIds, lang }),
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(
      typeof detail === 'string' ? detail : 'Failed to export training set',
    );
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'training-set.zip';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Translate the asset's generation prompt into `targetLang` via the
 *  user's assigned translation agent. Returns both prompt sides. */
export async function translateGenPrompt(
  resourceId: string,
  targetLang: 'en' | 'zh',
): Promise<{
  gen_prompt: string | null;
  gen_prompt_zh: string | null;
  gen_prompt_negative?: string | null;
  gen_prompt_negative_zh?: string | null;
}> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/gen-prompt/translate`,
    {
      method: 'POST',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_lang: targetLang }),
    },
  );
  if (!response.ok) {
    // The backend envelope is ErrorResponse — `{success, error, code,
    // request_id, details}`; there is no `detail`. Provider failures arrive
    // here already classified (`code: 'provider_rate_limit'`, …), so the code
    // travels on the Error for callers that render typed copy.
    const body = await response.json().catch(() => null);
    const err: Error & { code?: string } = new Error(
      body?.error || 'Failed to translate prompt',
    );
    if (typeof body?.code === 'string') err.code = body.code;
    throw err;
  }
  const json = await response.json();
  return json.data;
}

export async function setResourceChorus(
  resourceId: string,
  chorusMs: number | null,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/chorus`, {
    method: 'PUT',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ chorus_start_ms: chorusMs }),
  });
  if (!response.ok) throw new Error('Failed to set chorus');
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
  // Range-drained: a folder can exceed PostgREST's 1000-row cap, which a bare
  // select() silently truncates. Stable (created_at, id) order keeps pages
  // from shifting mid-drain; 10k safety ceiling per fetchAllRows.
  return fetchAllRows<ResourceItem>((from, to) => {
    let query = supabase
      .from('resource_items')
      .select('*, resource:resources!inner(*)')
      .eq('folder_id', folderId);
    if (!includeTrashed) {
      query = query.eq('resource.is_trashed', false);
    }
    return query
      .order('created_at', { ascending: false })
      .order('id', { ascending: false })
      .range(from, to);
  });
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
  /** Flatten/recursive mode — ignore the single-folder constraint so files from
   *  the current folder AND all descendant folders surface in one flat list
   *  (used by the "show child files" toggle + recursive search). */
  flatten?: boolean;
  /** When `flatten` and inside a folder: the {current + all descendant} folder
   *  ids to scope to (folder_id IN …). Empty/undefined at root means "no folder
   *  constraint" → every file in the scope/library. */
  flattenFolderIds?: string[];
  /** Server-side keyword search on resources.filename (ILIKE). Makes search hit
   *  the database across the whole (flattened) scope instead of only filtering
   *  the already-loaded page — fixes "search returns nothing for items beyond
   *  the first page". */
  search?: string;
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
  /** True means "any of gen_prompt / gen_prompt_negative / gen_prompt_json /
   *  slide_prompts is non-empty". Not a status column — applied via a
   *  4-column OR + jsonb-literal predicate on the `resources` embed (see
   *  buildResourceItemsQuery). Only ``tag_ids`` forces the RPC path
   *  (fetchResourcesViaRpc); this filter stays on the PostgREST path so
   *  ``search``/``flatten`` keep working when combined with it. */
  ai_has_prompt?: boolean;
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
  /** Explicit row cap. Without it PostgREST still hard-caps at 1000 —
   *  callers that only need a page should say so instead of relying on the
   *  silent ceiling. Full-set callers belong on fetchResourcesPaginated. */
  limit?: number;
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
 * PostgREST ``.or()`` expression for the "Has Prompt" filter — any of the 3
 * TEXT prompt columns (gen_prompt / gen_prompt_negative / gen_prompt_json;
 * gen_prompt_json is TEXT despite the name — migration 392 — confirmed via
 * information_schema) is non-blank, OR slide_prompts (JSONB) carries a
 * non-empty value. Mirrors the RPC predicate added in migration 401
 * (search_scope_resources / rpc_downloads_library_search) so the two code
 * paths agree on what "has a prompt" means.
 *
 * Column-type quirks baked in here, all reconfirmed against a live
 * PostgREST v14.8 instance (matching the review's repro environment) before
 * landing — nested and()/or() list-context filter values behave differently
 * from flat query-param values:
 *   - Text columns: ``col.match.[^\s]`` (regex "has a non-whitespace char")
 *     is the nested-list-context equivalent of SQL's ``btrim(col) <> ''``
 *     — plain ``.neq.`` alone would accept a whitespace-only value.
 *   - Text columns also exclude the literal serialized-empty strings
 *     ``[]``/``""`` (defensive — gen_prompt_json stores JSON *as text*, so a
 *     stringified empty array/string would otherwise read as "has a
 *     prompt"). No known production rows hit this today (M1, minor).
 *   - slide_prompts (jsonb): excludes the JSON literals ``null``/``{}``/``[]``.
 *     A literal ``""`` (empty-string) exclusion was tried and dropped: inside
 *     a nested and()/or() list, PostgREST's quote-stripping collides with the
 *     jsonb cast and 400s with "invalid input syntax for type json" — a
 *     PostgREST v14.8 parser limitation, not an app bug. No known production
 *     slide_prompts row is the empty-string literal, so this gap is inert.
 *
 * Verified end-to-end via real HTTP against the local PG17 + PostgREST
 * v14.8 stack (nous-db / nous-rest, through nous-kong): querying
 * `resource_items?select=*,resources!inner(*)&resources.or=(<this
 * expression>)` against a scope containing the 3 known prompt-bearing rows
 * returned exactly those 3 rows (2 via gen_prompt, 1 via slide_prompts) —
 * same count the migration's SQL predicate and the original review's
 * production read-only check both report.
 */
export const HAS_PROMPT_OR_EXPRESSION = [
  'and(gen_prompt.match.[^\\s],gen_prompt.neq.[],gen_prompt.neq."")',
  'and(gen_prompt_negative.match.[^\\s],gen_prompt_negative.neq.[],gen_prompt_negative.neq."")',
  'and(gen_prompt_json.match.[^\\s],gen_prompt_json.neq.[],gen_prompt_json.neq."")',
  'and(slide_prompts.not.is.null,slide_prompts.neq.null,slide_prompts.neq.{},slide_prompts.neq.[])',
].join(',');

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

  // Gallery membership is needed to hide child rows / stamp counts (the direct
  // query can't; gallery_items is service-role only). Fetch it in parallel.
  const membershipPromise = fetchGalleryScopeMembership(params.scopeId);

  const query = buildResourceItemsQuery(params, tagResourceIds);
  let ordered = query.order('created_at', { ascending: false });
  if (params.limit != null) ordered = ordered.limit(params.limit);
  const { data, error } = await ordered;
  if (error) throw error;
  // The query builder's return type narrows as we chain filters; cast back.
  const rows = (data as unknown as ResourceItem[]) ?? [];
  return applyGalleryMembership(rows, await membershipPromise);
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

  if (params.flatten) {
    // Recursive/flat mode: scope to {current + descendant} folders, or drop the
    // folder constraint entirely at root (whole scope/library).
    if (params.flattenFolderIds && params.flattenFolderIds.length > 0) {
      query = query.in('folder_id', params.flattenFolderIds);
    }
  } else if (params.folderId) {
    query = query.eq('folder_id', params.folderId);
  } else {
    query = query.is('folder_id', null);
  }

  if (params.libraryId) {
    query = query.eq('library_id', params.libraryId);
  } else if (!params.isPersonal) {
    query = query.is('library_id', null);
  }

  // ── Server-side keyword search (resources.filename OR resources.notes ILIKE).
  //    Both columns carry pg_trgm GIN indexes (filename: mig 300, notes: mig
  //    154), so `ILIKE '%q%'` is index-backed and fast at scale. PostgREST's
  //    or() value grammar splits only on the first two dots + top-level commas,
  //    so dotted filenames ("image.jpg") are safe in the value; we strip just
  //    commas/parens (which would break the logic-tree parse). `*` = wildcard. ──
  if (params.search && params.search.trim()) {
    const safe = params.search.trim().replace(/[(),]/g, ' ').replace(/\s+/g, ' ').trim();
    if (safe) {
      query = query.or(`filename.ilike.*${safe}*,notes.ilike.*${safe}*`, {
        referencedTable: 'resources',
      });
    }
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

  // ── "Has prompt": any of the 3 text columns non-blank, or slide_prompts
  //    (jsonb) non-empty. Mirrors the RPC predicate in migration 401. ──
  if (params.ai_has_prompt) {
    query = query.or(HAS_PROMPT_OR_EXPRESSION, { referencedTable: 'resources' });
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
  //
  // ai_has_prompt does NOT force the RPC path on its own (fixed post-review —
  // it used to, but that silently dropped `search`/`flatten`: the RPC has no
  // p_search/p_flatten param, so typing a filename then checking "Has Prompt"
  // returned every prompt-bearing resource, search term ignored). The 4-column
  // OR + jsonb-literal predicate IS expressible via the PostgREST builder
  // (see the `params.ai_has_prompt` branch in buildResourceItemsQuery below),
  // verified against a live PostgREST v14.8 instance. Only tags still force
  // the RPC (no resource_id-intersection alternative at scale); when both tags
  // AND has_prompt are active, search_scope_resources (mig 401) still carries
  // p_has_prompt so the combination stays correct — search/flatten were never
  // available in the tag-filtered RPC path anyway (pre-existing, out of scope).
  if (params.tag_ids && params.tag_ids.length > 0) {
    return fetchResourcesViaRpc(params, cursor, pageSize, signal);
  }

  // Gallery membership (hide child rows / stamp counts) — the direct query
  // can't reproduce it because gallery_items is service-role only. Fetch it in
  // parallel with the page query.
  const membershipPromise = fetchGalleryScopeMembership(params.scopeId);

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
  // Slice for keyset FIRST (hasMore / nextCursor derive from the RAW rows, so
  // pagination progresses correctly even when a whole window is gallery
  // children), THEN drop children from the visible page. A filtered page may be
  // shorter than pageSize; that is fine — the loader keeps advancing.
  const page = sliceKeysetPage(rows, pageSize, (row) => {
    const r = row as { id?: string | number; created_at?: string };
    return r.created_at && r.id != null
      ? { ts: r.created_at, id: String(r.id) }
      : null;
  });
  page.data = applyGalleryMembership(page.data, await membershipPromise);

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
    p_has_prompt: params.ai_has_prompt ?? null,
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

  const membershipPromise = fetchGalleryScopeMembership(params.scopeId);

  const { data, error } = await req;
  if (error) throw error;

  const result = (data ?? { rows: [], total_count: null }) as {
    rows: ResourceItem[];
    total_count: number | null;
  };
  const rows = result.rows ?? [];
  // Slice for keyset first (cursor from raw rows), then hide gallery children —
  // the RPC (mig 269) predates the gallery entity and has no child exclusion.
  const page = sliceKeysetPage(rows, pageSize, (row) => {
    const r = row as { id?: string | number; created_at?: string };
    return r.created_at && r.id != null
      ? { ts: r.created_at, id: String(r.id) }
      : null;
  });
  page.data = applyGalleryMembership(page.data, await membershipPromise);
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

/**
 * Batch duplicate check — POSTs up to 100 items (matching the backend cap) and
 * returns one result per input item.
 *
 * Fail-open: on any network or server error every item is returned as
 * non-duplicate so a dedup outage never blocks an upload.
 */
export async function checkDuplicatesBatch(
  items: { file_hash: string; file_size: number }[],
): Promise<{ file_hash: string; duplicate: boolean; existing: Resource | null }[]> {
  const apiUrl = getApiUrl();
  const failOpen = () =>
    items.map((i) => ({ file_hash: i.file_hash, duplicate: false, existing: null }));
  try {
    const response = await fetch(`${apiUrl}/api/v1/resources/check-duplicates`, {
      method: 'POST',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    if (!response.ok) return failOpen();
    // The endpoint returns an envelope `{ results: [...] }` (CheckDuplicatesResponse).
    // Accept a bare array too so either shape stays fail-open — a shape drift
    // here once threw "not iterable" downstream and blocked ALL uploads.
    const data = await response.json();
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.results)) return data.results;
    console.error('checkDuplicatesBatch: unexpected response shape', data);
    return failOpen();
  } catch (err) {
    console.error('checkDuplicatesBatch failed', err);
    return failOpen();
  }
}

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

/**
 * One progress sample from an upload in flight.
 *
 * ⚠️ RAW NUMBERS AND A CLOCK READING — NO RATE, NO PERCENTAGE, NO ETA.
 *
 * A rate is a display decision: how long a window to average over, when a
 * stall counts as zero rather than as "no reading", how much to smooth so the
 * figure does not flicker. Those are answers about what a person can read off
 * a screen, and the service layer has no business holding an opinion about
 * them. It reports what the browser told it, plus WHEN it was told, which is
 * the one piece a caller cannot recover afterwards.
 *
 * The predecessor of this type was `(progress: number) => void` carrying a
 * rounded percentage. A percentage is lossy in exactly the direction that
 * matters here — it cannot be turned back into bytes, so no caller could ever
 * have derived a rate from it no matter how it was smoothed.
 */
export interface UploadProgressSample {
  /** Bytes handed to the socket so far, as reported by the browser. */
  loaded: number;
  /**
   * Total bytes of the request body, or null when the browser says it cannot
   * compute one (`lengthComputable === false`).
   *
   * Null rather than a fallback to `file.size`: the request body is the file
   * plus multipart framing, so `file.size` is a different number, and quietly
   * substituting it would report a denominator the browser never agreed to.
   * A caller that wants to estimate from `file.size` can — knowingly.
   */
  total: number | null;
  /**
   * `Date.now()` when the sample was taken.
   *
   * Carried rather than left for the caller to read on arrival, because the
   * two are not the same instant: samples can be delivered in a burst after a
   * long task blocks the main thread, and a caller timestamping on receipt
   * would compute a rate from the gap between two deliveries instead of the
   * gap between two measurements.
   */
  at: number;
}

/**
 * Why an upload did not produce a resource.
 *
 * Typed rather than prose because the caller has to DO different things:
 * `aborted` is not a failure to report at all, `unauthorized` and `too_large`
 * are the user's to fix, `network` is worth retrying, `server` is ours.
 *
 * The thing this replaces was `new Error('Failed to upload resource')` for
 * every non-2xx — a sentence that says nothing, on the one path where the
 * backend had already said something specific (`413 File too large. Maximum
 * size is 500 MB.` arrives as a `detail` and used to be thrown away).
 */
export type UploadFailureReason =
  | 'aborted'
  | 'network'
  | 'unauthorized'
  | 'too_large'
  | 'rejected'
  | 'server'
  | 'malformed';

/** A failed upload, with enough on it for the caller to say something true. */
export class ResourceUploadError extends Error {
  readonly reason: UploadFailureReason;

  /** HTTP status, or null when the request never got an answer. */
  readonly status: number | null;

  /** The backend's own `detail`, when it sent one. Never invented. */
  readonly detail: string | null;

  constructor(
    reason: UploadFailureReason,
    status: number | null,
    detail: string | null,
    /* The original throw, when there was one. Kept so a network failure's real
       cause is not erased by the act of classifying it. */
    options?: { cause?: unknown },
  ) {
    super(detail ?? `Upload failed (${reason})`, options);
    this.name = 'ResourceUploadError';
    this.reason = reason;
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Classify a completed-but-unsuccessful upload response.
 *
 * Exported so the mapping is testable on its own: it is a table, and a table
 * that is only ever exercised through a mocked XHR is a table nobody checks.
 */
export function classifyUploadStatus(status: number): UploadFailureReason {
  if (status === 401 || status === 403) return 'unauthorized';
  if (status === 413) return 'too_large';
  if (status >= 500) return 'server';
  return 'rejected';
}

/**
 * The backend's own sentence for a failed request, or null.
 *
 * Two envelopes reach here: FastAPI's `HTTPException` → `{"detail": "..."}`,
 * and the `AppError` handler → `{"error": "...", "code": "..."}` (typed
 * domain failures — e.g. `object_store_write_failed`, 2026-09-07). Reading
 * only `detail` made the one failure the backend explains best arrive as
 * "Upload failed (server)".
 */
export function errorMessageFromBody(body: string): string | null {
  try {
    const parsed = JSON.parse(body);
    const detail = parsed?.detail;
    if (typeof detail === 'string' && detail !== '') return detail;
    const error = parsed?.error;
    if (typeof error === 'string' && error !== '') return error;
    return null;
  } catch {
    return null;
  }
}

/** `errorMessageFromBody` over a non-2xx Response, with a fallback sentence. */
async function errorMessageFromResponse(response: Response, fallback: string): Promise<string> {
  const text = await response.text().catch(() => '');
  return errorMessageFromBody(text) ?? fallback;
}

const uploadDetail = errorMessageFromBody;

/**
 * Upload one file into a scope's library.
 *
 * `onProgress` selects the XHR path — `fetch` cannot report upload progress at
 * all, which is why the two implementations exist. `signal` works on BOTH
 * paths: a cancel button that only stops the UI while the bytes keep going is
 * not a cancel button, it is a lie about one.
 */
export async function uploadResource(
  file: File,
  scopeId: string,
  folderId?: string | null,
  onProgress?: (sample: UploadProgressSample) => void,
  libraryId?: string | null,
  signal?: AbortSignal,
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
      /* Checked BEFORE opening the request, not only wired to the event: an
         already-aborted signal would otherwise send the whole file and cancel
         it a moment later, which is the bytes-on-the-wire version of the bug
         the signal exists to prevent. */
      if (signal?.aborted === true) {
        reject(new ResourceUploadError('aborted', null, null));
        return;
      }
      const onAbort = () => xhr.abort();
      signal?.addEventListener('abort', onAbort);
      const done = () => signal?.removeEventListener('abort', onAbort);

      xhr.upload.addEventListener('progress', (e) => {
        onProgress({
          loaded: e.loaded,
          total: e.lengthComputable ? e.total : null,
          at: Date.now(),
        });
      });
      xhr.addEventListener('load', () => {
        done();
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText).data);
          } catch {
            /* A 2xx we cannot read is its own failure and says so, rather than
               surfacing as a SyntaxError from inside a promise executor. */
            reject(new ResourceUploadError('malformed', xhr.status, null));
          }
          return;
        }
        reject(new ResourceUploadError(
          classifyUploadStatus(xhr.status),
          xhr.status,
          uploadDetail(xhr.responseText),
        ));
      });
      xhr.addEventListener('error', () => {
        done();
        reject(new ResourceUploadError('network', null, null));
      });
      xhr.addEventListener('abort', () => {
        done();
        reject(new ResourceUploadError('aborted', null, null));
      });
      xhr.open('POST', `${apiUrl}/api/v1/resources/upload?${params}`);
      Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
      xhr.send(formData);
    });
  }

  let response: Response;
  try {
    response = await fetch(`${apiUrl}/api/v1/resources/upload?${params}`, {
      method: 'POST',
      headers,
      body: formData,
      signal,
    });
  } catch (err) {
    if (signal?.aborted === true) throw new ResourceUploadError('aborted', null, null, { cause: err });
    throw new ResourceUploadError('network', null, null, { cause: err });
  }
  if (!response.ok) {
    throw new ResourceUploadError(
      classifyUploadStatus(response.status),
      response.status,
      uploadDetail(await response.text().catch(() => '')),
    );
  }
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


/**
 * Keyset-paginated recycle bin: resources with `is_trashed=true` filtered by
 * the `last_scope_type/last_scope_id` restore-location snapshot, ordered
 * `(trashed_at DESC, id DESC)` so it scrolls past PostgREST's 1000-row cap.
 * No search param — the recycle view has no filter bar. First-page count on
 * `cursor === null`. (The old unpaginated `fetchTrashedResources` is gone —
 * it silently capped at 1000.)
 */
export async function fetchTrashedResourcesPaginated(
  isPersonal: boolean,
  scopeId: string,
  cursor: KeysetCursor | null,
  pageSize: number,
  signal?: AbortSignal,
): Promise<KeysetListPage<ResourceItem>> {
  let q = supabase
    .from('resources')
    .select('*')
    .eq('is_trashed', true)
    .eq('last_scope_type', isPersonal ? 'personal' : 'team')
    .eq('last_scope_id', scopeId)
    .order('trashed_at', { ascending: false })
    .order('id', { ascending: false });
  q = applyKeysetCursor(q, cursor, pageSize, { tsCol: 'trashed_at', idCol: 'id' });
  if (signal) q = q.abortSignal(signal);

  const { data, error } = await q;
  if (error) throw error;

  // Slice on the RAW resource rows — the cursor reads trashed_at/id BEFORE the
  // map flattens them into the ResourceItem wrapper.
  const rows = (data ?? []) as Array<Record<string, unknown>>;
  const page = sliceKeysetPage(rows, pageSize, (r) =>
    r.trashed_at && r.id != null
      ? { ts: r.trashed_at as string, id: String(r.id) }
      : null,
  );
  const items = page.data.map((resource): ResourceItem => ({
    id: resource.id as string,
    resource_id: resource.id as string,
    scope_id: scopeId,
    folder_id: (resource.last_folder_id as string | null) ?? null,
    library_id: (resource.last_library_id as string | null) ?? null,
    added_by: (resource.created_by as string | null) ?? null,
    created_at: resource.created_at as string,
    resource: resource as unknown as Resource,
  }));

  let totalCount = -1;
  if (cursor === null) {
    const { count } = await supabase
      .from('resources')
      .select('id', { count: 'exact', head: true })
      .eq('is_trashed', true)
      .eq('last_scope_type', isPersonal ? 'personal' : 'team')
      .eq('last_scope_id', scopeId);
    totalCount = count ?? -1;
  }

  return { data: items, hasMore: page.hasMore, nextCursor: page.nextCursor, totalCount };
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
  if (!response.ok) {
    throw new Error(await errorMessageFromResponse(response, 'Failed to upload new version'));
  }
  const json = await response.json();
  return json.data;
}

/**
 * Save edited text as a NEW version. Builds a File from the string and reuses
 * the existing multipart version-upload endpoint (content-addressed under
 * unified storage). Used by the text-resource editor's "Save as new version".
 */
export async function saveTextAsNewVersion(
  resourceId: string,
  text: string,
  filename: string,
  mime: string,
  notes?: string,
): Promise<ResourceVersion> {
  const file = new File([text], filename, { type: mime || 'text/plain' });
  return uploadNewVersion(resourceId, file, notes);
}

/**
 * OVERWRITE the current version's bytes in place (no new version row).
 * Used by the text-resource editor's "Overwrite current version".
 */
export async function overwriteVersionContent(
  resourceId: string,
  versionId: string,
  text: string,
  filename: string,
  mime: string,
): Promise<ResourceVersion> {
  const apiUrl = getApiUrl();
  const file = new File([text], filename, { type: mime || 'text/plain' });
  const formData = new FormData();
  formData.append('file', file);

  const headers: Record<string, string> = {};
  const authHeaders = await getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/content`,
    { method: 'PUT', headers, body: formData },
  );
  if (!response.ok) {
    throw new Error(await errorMessageFromResponse(response, 'Failed to overwrite version'));
  }
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
  // Range-drained past the 1000-row cap (downloads sidebar view can exceed
  // it long before the primary keyset-paginated views do).
  return fetchAllRows<ResourceItem>((from, to) =>
    supabase
      .from('resource_items')
      .select('*, resource:resources!inner(*)')
      .eq('scope_id', scopeId)
      .eq('resource.source_type', 'web')
      .order('created_at', { ascending: false })
      .order('id', { ascending: false })
      .range(from, to),
  );
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

/**
 * Resolve a smart-folder `relative:-7d` value to an absolute ISO timestamp.
 * Mirrors the backend `ResourcesRepository._resolve_value` so the value the
 * RPC receives is already an absolute date it can cast with `::timestamptz`.
 * Units: d = days, h = hours, m = minutes (default days). Non-relative values
 * pass through unchanged.
 */
function resolveRelativeDate(value: string): string {
  if (!value.startsWith('relative:')) return value;
  const offsetStr = value.split(':')[1] ?? '';
  const unit = offsetStr.slice(-1);
  const amount = parseInt(offsetStr.slice(0, -1), 10);
  if (Number.isNaN(amount)) return value;
  const msPerUnit = unit === 'h' ? 3_600_000 : unit === 'm' ? 60_000 : 86_400_000;
  return new Date(Date.now() + amount * msPerUnit).toISOString();
}

/**
 * Keyset-paginated smart-folder evaluation via the `search_smart_folder` RPC
 * (mig 276). Replaces the legacy drain-all `GET /smart-folders/{id}/results`,
 * which silently capped at PostgREST's 1000-row ceiling once a smart folder
 * matched more than 1000 resources.
 *
 * Calls the RPC DIRECTLY through the supabase client (not the backend) so
 * `bigIntSafeFetch` keeps Snowflake ids precision-safe in the jsonb rows — the
 * same pattern as `fetchResourcesViaRpc`. RLS (SECURITY INVOKER on the RPC)
 * enforces scope access. Relative-date conditions are resolved to absolute
 * timestamps here before dispatch, parity with the legacy Python path.
 */
export async function fetchSmartFolderResultsPaginated(
  scopeId: string,
  rules: SmartFolderRules,
  cursor: KeysetCursor | null,
  pageSize: number,
  signal?: AbortSignal,
): Promise<KeysetListPage<ResourceItem>> {
  const resolvedRules = {
    operator: rules.operator,
    match: rules.match,
    conditions: (rules.conditions || []).map((c) => ({
      field: c.field,
      op: c.op,
      value: resolveRelativeDate(String(c.value)),
    })),
  };

  let req = supabase.rpc('search_smart_folder', {
    p_scope_id: scopeId,
    p_rules: resolvedRules,
    p_search: null,
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
  if (resourceItemIds.length === 0) return;
  // Chunk the id list so a bulk move of >1000 items doesn't blow past the
  // gateway URL/header ceiling (PATCH carries the `.in()` filter in the URL).
  // Sequential to keep the write load predictable; accumulate affected rows.
  let affected = 0;
  for (const ids of chunked(resourceItemIds, PG_IN_CHUNK)) {
    const { data, error } = await supabase
      .from('resource_items')
      .update(update)
      .in('id', ids)
      .select('id');
    if (error) throw error;
    affected += data?.length ?? 0;
  }
  if (affected === 0) {
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
    // BFS to collect all descendant folder IDs. Each level's `.in('parent_id')`
    // probe is chunked so a wide subtree (>1000 siblings at one level) can't
    // overflow the gateway URL ceiling.
    const allFolderIds = [folderId];
    let queue = [folderId];
    while (queue.length > 0) {
      const childIds: string[] = [];
      for (const parentChunk of chunked(queue, PG_IN_CHUNK)) {
        const { data: children, error: childErr } = await supabase
          .from('folders')
          .select('id')
          .in('parent_id', parentChunk);
        if (childErr) throw childErr;
        if (children) childIds.push(...children.map((c: any) => String(c.id)));
      }
      if (childIds.length === 0) break;
      allFolderIds.push(...childIds);
      queue = childIds;
    }

    // Update sub-folders' library_id (chunked — deep tree can exceed 1000).
    const subFolderIds = allFolderIds.slice(1);
    for (const ids of chunked(subFolderIds, PG_IN_CHUNK)) {
      const { error: folderErr } = await supabase
        .from('folders')
        .update({ library_id: targetLibraryId })
        .in('id', ids);
      if (folderErr) throw folderErr;
    }

    // Update all resource_items in affected folders (chunked on folder_id).
    for (const ids of chunked(allFolderIds, PG_IN_CHUNK)) {
      const { error: itemErr } = await supabase
        .from('resource_items')
        .update({ library_id: targetLibraryId })
        .in('folder_id', ids);
      if (itemErr) throw itemErr;
    }
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

// ─── Gallery (first-class gallery entity, PR-A) ──────────────────────

/** Mime type marking a resource as a first-class gallery entity (PR-A). A
 *  gallery is one `resources` row; its ordered child images are ordinary
 *  image resources linked through the `gallery_items` junction. */
export const GALLERY_MIME = 'application/x-mediahub-gallery';

/** Ordered child image of a gallery, as returned by the gallery-items API. */
export interface GalleryChildItem {
  id: string;
  filename: string;
  thumbnail_path: string | null;
  position: number;
}

/** Create an empty gallery entity in a scope. Children are attached
 *  separately via {@link setGalleryItems}. Returns the gallery resource. */
export async function createGallery(
  scopeId: string,
  filename: string,
  folderId?: string | null,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_id: scopeId, filename });
  if (folderId) params.set('folder_id', folderId);

  const response = await fetch(`${apiUrl}/api/v1/resources/galleries?${params}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to create gallery');
  const json = await response.json();
  return json.data;
}

/** Replace a gallery's ordered children with ``imageIds`` (full reset). Every
 *  id must be an image resource in ``scopeId``. Returns the new child list. */
export async function setGalleryItems(
  galleryId: string,
  scopeId: string,
  imageIds: string[],
): Promise<GalleryChildItem[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_id: scopeId });
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${galleryId}/gallery-items?${params}`,
    {
      method: 'PUT',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_ids: imageIds }),
    },
  );
  if (!response.ok) throw new Error('Failed to set gallery items');
  const json = await response.json();
  // Children just changed → drop cached membership so the next library list
  // reflects the new hidden-child set and badge count immediately.
  invalidateGalleryMembership(scopeId);
  return json.data ?? [];
}

/** Read a gallery's ordered child images. */
export async function getGalleryItems(
  galleryId: string,
): Promise<GalleryChildItem[]> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/resources/${galleryId}/gallery-items`,
    { headers: await getAuthHeaders() },
  );
  if (!response.ok) throw new Error('Failed to load gallery items');
  const json = await response.json();
  return json.data ?? [];
}

// ── Gallery membership (direct-query library list support) ──────────────
//
// The main Resources library list runs a direct supabase-js query
// (fetchResources / fetchResourcesPaginated) — NOT the backend list endpoint —
// so it never sees the backend's ``NOT EXISTS (... gallery_items ...)`` child
// exclusion or its computed ``gallery_count`` column. And ``gallery_items`` is
// a service-role-only table (RLS lockdown, mig 375), so the anon frontend
// client cannot read it directly to reproduce that logic. This tiny backend
// read endpoint hands the frontend exactly the two derived bits it needs; the
// list then hides child rows and annotates gallery tiles client-side.

/** Per-scope gallery membership: the child resource ids to hide from the
 *  library list, and per-gallery child counts for the ▣ badge. */
export interface GalleryScopeMembership {
  /** resources.id of every image that is a child of some gallery in scope. */
  childIds: Set<string>;
  /** gallery resources.id → child count. */
  counts: Map<string, number>;
}

const EMPTY_GALLERY_MEMBERSHIP: GalleryScopeMembership = {
  childIds: new Set(),
  counts: new Map(),
};

// Cache membership per scope so a keyset "load more" burst does not refetch on
// every page. A short TTL keeps a freshly-created gallery's children hiding
// within a few seconds; gallery mutations invalidate the entry explicitly.
const GALLERY_MEMBERSHIP_TTL_MS = 5000;
const galleryMembershipCache = new Map<
  string,
  { at: number; promise: Promise<GalleryScopeMembership> }
>();

/** Drop the cached membership for a scope (all scopes when omitted) so the next
 *  list load reflects a just-created / edited / deleted gallery immediately. */
export function invalidateGalleryMembership(scopeId?: string): void {
  if (scopeId) galleryMembershipCache.delete(scopeId);
  else galleryMembershipCache.clear();
}

async function requestGalleryMembership(
  scopeId: string,
): Promise<GalleryScopeMembership> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_id: scopeId });
  const response = await fetch(
    `${apiUrl}/api/v1/resources/gallery-membership?${params}`,
    { headers: await getAuthHeaders() },
  );
  if (!response.ok) throw new Error('Failed to load gallery membership');
  const json = await response.json();
  const data = (json.data ?? {}) as {
    child_image_ids?: unknown[];
    gallery_counts?: Record<string, unknown>;
  };
  return {
    childIds: new Set<string>((data.child_image_ids ?? []).map(String)),
    counts: new Map<string, number>(
      Object.entries(data.gallery_counts ?? {}).map(
        ([k, v]) => [String(k), Number(v)] as [string, number],
      ),
    ),
  };
}

/** Fetch (or reuse cached) gallery membership for a scope. Fails open: any
 *  hiccup returns empty membership so the library still renders (all rows
 *  visible, badge 0) rather than blanking. */
export async function fetchGalleryScopeMembership(
  scopeId: string | undefined | null,
): Promise<GalleryScopeMembership> {
  if (!scopeId) return EMPTY_GALLERY_MEMBERSHIP;
  const now = Date.now();
  const cached = galleryMembershipCache.get(scopeId);
  if (cached && now - cached.at < GALLERY_MEMBERSHIP_TTL_MS) {
    return cached.promise;
  }
  const promise = requestGalleryMembership(scopeId).catch((err) => {
    console.error('[gallery] membership fetch failed:', err);
    galleryMembershipCache.delete(scopeId);
    return EMPTY_GALLERY_MEMBERSHIP;
  });
  galleryMembershipCache.set(scopeId, { at: now, promise });
  return promise;
}

/** Hide gallery-child rows and stamp gallery rows with their real child count.
 *  Pure + immutable — used by every list path (keyset, RPC, bulk). Rows whose
 *  embedded resource id is a known child are dropped; gallery rows get a fresh
 *  copy carrying ``gallery_count`` (the direct query cannot compute it). */
export function applyGalleryMembership(
  rows: ResourceItem[],
  membership: GalleryScopeMembership,
): ResourceItem[] {
  if (membership.childIds.size === 0 && membership.counts.size === 0) {
    return rows;
  }
  const kept: ResourceItem[] = [];
  for (const row of rows) {
    const resourceId =
      row.resource?.id != null ? String(row.resource.id) : null;
    if (resourceId && membership.childIds.has(resourceId)) continue;
    const count = resourceId ? membership.counts.get(resourceId) : undefined;
    if (count !== undefined && row.resource) {
      kept.push({ ...row, resource: { ...row.resource, gallery_count: count } });
    } else {
      kept.push(row);
    }
  }
  return kept;
}

// ─── Canvas back-references ─────────────────────────────
// Moved here from the retired `projectAssetsService` (P1 generated inbox,
// 2026-08-29): the Project Assets view is gone, but the resource detail
// panel's "Appears in N canvases" section still reads this endpoint.

export interface CanvasBackRef {
  canvas_id: string;
  canvas_name: string;
  kind: 'smart' | 'classic';
  project_id: string;
  role: 'reference' | 'output';
}

/** Canvases that reference this resource, with the role it plays in each. */
export async function fetchResourceCanvasRefs(resourceId: string): Promise<CanvasBackRef[]> {
  const res = await fetch(`${getApiUrl()}/api/v1/resources/${resourceId}/canvas-refs`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  return json.data as CanvasBackRef[];
}
