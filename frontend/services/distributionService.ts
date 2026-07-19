import { SocialAccount, PublishRequest, PublishTask, LibraryVideo } from '../types';
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getApiUrl()}/api/v1/distribution${path}`, {
    ...init,
    headers: { ...(await getAuthHeaders()), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    throw new Error(`distribution api ${path} failed: ${res.status}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const listAccounts = (): Promise<SocialAccount[]> =>
  request<{ accounts: SocialAccount[] }>('/accounts').then((r) => r.accounts);

export const connectAccount = (body: {
  platform: string;
  scope_type: 'user' | 'team';
  scope_id: string;
}): Promise<{ auth_url: string }> =>
  request<{ auth_url: string }>('/accounts/connect', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const refreshAccount = (id: string): Promise<SocialAccount> =>
  request<SocialAccount>(`/accounts/${id}/refresh`, { method: 'POST' });

export const deleteAccount = (id: string): Promise<void> =>
  request<void>(`/accounts/${id}`, { method: 'DELETE' });

/**
 * Distribution module switches (admin-controlled, DB-backed — mirrors the
 * Topic Inspiration `module-status`). `visible` gates the nav entry + routes;
 * `enabled` reports whether the account/OAuth API is reachable. Both default
 * OFF (opt-in): on any read error we fail CLOSED so the not-yet-launched
 * module never flashes into view.
 */
export interface DistributionModuleStatus {
  enabled: boolean;
  visible: boolean;
}

export const getModuleStatus = async (): Promise<DistributionModuleStatus> => {
  try {
    const d = await request<Partial<DistributionModuleStatus>>('/module-status');
    return { enabled: d.enabled === true, visible: d.visible === true };
  } catch (err) {
    console.error('distribution: module-status load failed', err);
    return { enabled: false, visible: false };
  }
};

export const createPublishTask = (body: PublishRequest): Promise<PublishTask> =>
  request<PublishTask>('/tasks', { method: 'POST', body: JSON.stringify(body) });

export const listPublishTasks = (): Promise<PublishTask[]> =>
  request<{ tasks: PublishTask[] }>('/tasks').then((r) => r.tasks);

export const getPublishTask = (id: string): Promise<PublishTask> =>
  request<PublishTask>(`/tasks/${id}`);

export const cancelPublishTask = (id: string): Promise<PublishTask> =>
  request<PublishTask>(`/tasks/${id}/cancel`, { method: 'POST' });

export const retryPublishTask = (id: string): Promise<PublishTask> =>
  request<PublishTask>(`/tasks/${id}/retry`, { method: 'POST' });

export const getShareSchema = (
  id: string,
): Promise<{ schema_url: string; share_id: string }> =>
  request<{ schema_url: string; share_id: string }>(`/tasks/${id}/share-schema`);

/**
 * Content source for the Publish page — recent video resources from the
 * Library. Goes through `request` (reuses auth headers) and hops out of the
 * /distribution prefix via `/../resources` (standards-compliant URL path
 * normalization → /api/v1/resources). `all_folders=true` because the picker
 * is scope-wide — without it the endpoint returns ROOT-level items only and
 * silently misses every video the user filed into a folder. Capped at the
 * newest 500 so huge libraries can't flood the response (search happens
 * client-side within that window). Fails soft to [] so the page renders.
 */
/**
 * One row of `GET /api/v1/resources` (backend `get_resource_items`). The row
 * is a `resource_items` row — its top-level `id` is the JOIN row id, NOT the
 * resource id. The real resource id is `resource_id`, and the file fields live
 * in the nested `resource` object (backend `row_to_json(r.*) AS resource`).
 * Reading `filename`/`thumbnail_path` off the top level (the old bug) always
 * yielded `undefined` → every tile fell back to "Untitled" + placeholder, and
 * publishing later failed because the picked ids were JOIN-row ids.
 */
interface ResourceItemRow {
  id?: unknown;
  resource_id?: unknown;
  resource?: {
    filename?: unknown;
    thumbnail_path?: unknown;
    mime_type?: unknown;
    gallery_count?: unknown;
  } | null;
}

export const listLibraryMedia = async (
  scopeId: string,
  opts?: { tagId?: string; mediaType?: 'video' | 'image' },
): Promise<LibraryVideo[]> => {
  try {
    const tagFilter = opts?.tagId ? `&tag_ids=${encodeURIComponent(opts.tagId)}` : '';
    // `types` maps to the backend mime filter: 'video' → mime LIKE 'video/%',
    // 'image' → mime LIKE 'image/%'. Defaults to video for back-compat.
    // In images mode we ALSO request `gallery` so first-class gallery entities
    // (mime 'application/x-mediahub-gallery') surface as their own picker rows
    // — FastAPI reads repeated keys as a List, so each value is its own param.
    const mediaType = opts?.mediaType ?? 'video';
    const typeFilter = mediaType === 'image'
      ? 'types=image&types=gallery'
      : `types=${mediaType}`;
    // Only the user's own content is publishable: keep uploads, AI/canvas
    // `generated` promote artifacts, and `derived` resources — exclude `web`
    // (platform parse/download material). FastAPI reads repeated keys as a
    // List, so each value is its own `source_types=` param.
    const ownContentFilter =
      '&source_types=upload&source_types=generated&source_types=derived';
    const res = await request<{ success: boolean; data: ResourceItemRow[] }>(
      `/../resources?scope_id=${encodeURIComponent(scopeId)}&${typeFilter}&all_folders=true&limit=500${ownContentFilter}${tagFilter}`,
    );
    const rows = res?.data ?? [];
    return rows.map((r) => {
      // Prefer the true resource id; fall back to the join-row id defensively.
      const resourceId = String(r.resource_id ?? r.id);
      const filename = r.resource?.filename;
      const mimeType = r.resource?.mime_type;
      const galleryCount = r.resource?.gallery_count;
      return {
        id: resourceId,
        filename: filename != null ? String(filename) : 'Untitled',
        thumbnail_url: r.resource?.thumbnail_path
          ? `${getApiUrl()}/api/v1/resources/${resourceId}/cover`
          : null,
        mime_type: mimeType != null ? String(mimeType) : null,
        gallery_count: typeof galleryCount === 'number' ? galleryCount : undefined,
      };
    });
  } catch (err) {
    console.error('distribution: list library media failed', err);
    return [];
  }
};

/**
 * Back-compat alias — existing callers select videos. New callers that need
 * images pass `{ mediaType: 'image' }` to `listLibraryMedia`.
 */
export const listLibraryVideos = listLibraryMedia;

/**
 * AI/canvas-generated videos (Tier-1 `generated_media`, personal scope).
 * They only become publishable `resources` after promote — the picker's
 * Generated tab lists them and promotes ON PICK (idempotent server-side via
 * the promoted_resource_id backlink). No cover endpoint exists for video
 * kind, so tiles render the placeholder gradient + prompt text.
 */
export interface GeneratedVideo {
  id: string;
  /** Display name — the generation prompt, or a fallback label. */
  name: string;
  created_at: string;
  /** Set when this generation was already promoted into the Library. */
  promoted_resource_id: string | null;
}

export const listGeneratedVideos = async (): Promise<GeneratedVideo[]> => {
  try {
    const res = await request<{
      data: { items?: Array<Record<string, unknown>>; next_cursor?: string | null };
    }>('/../generated-media?kind=video&limit=100');
    const items = res?.data?.items ?? [];
    return items.map((g) => ({
      id: String(g.id),
      name: typeof g.prompt === 'string' && g.prompt.trim() ? g.prompt.trim() : '',
      created_at: String(g.created_at ?? ''),
      promoted_resource_id: g.promoted_resource_id ? String(g.promoted_resource_id) : null,
    }));
  } catch (err) {
    console.error('distribution: list generated videos failed', err);
    return [];
  }
};

/** Promote a generated video into the Library; returns the resource id. */
export const promoteGeneratedVideo = async (genId: string): Promise<string> => {
  const res = await request<{ data: { promoted_resource_id: string } }>(
    `/../generated-media/${encodeURIComponent(genId)}/promote`,
    { method: 'POST' },
  );
  return String(res.data.promoted_resource_id);
};
