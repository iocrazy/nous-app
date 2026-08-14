import {
  SocialAccount, PublishRequest, PublishTask, LibraryVideo, CoverSelectResult,
} from '../types';
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

/**
 * Carries the HTTP status alongside the message so callers can tell apart
 * failures that need different words. Additive: the message format is
 * unchanged, so existing `catch (err) { console.error(err) }` sites behave
 * exactly as before.
 *
 * `detail` is the parsed `detail` field of the error body when the backend
 * sent one. Throwing away the body used to be the default, which meant a
 * typed rejection the backend had gone to the trouble of producing arrived at
 * the UI as nothing but a status code — and the page could only say "could
 * not create publish task". Same failure family as `attachment_failures`:
 * the backend returns the reason, nobody reads it.
 */
export class DistributionApiError extends Error {
  constructor(
    public readonly status: number,
    path: string,
    public readonly detail?: unknown,
  ) {
    super(`distribution api ${path} failed: ${status}`);
    this.name = 'DistributionApiError';
  }
}

/**
 * One typed reason the publish request was refused (backend `GateProblem`).
 *
 * `reason` is a stable code — the UI branches on it and never parses
 * `message`, which is English prose written for logs. `account_id` names the
 * account that caused it, or is null/absent for a whole-batch shape problem.
 */
export interface PublishGateProblem {
  reason: string;
  message: string;
  account_id?: string | null;
}

/** The `detail.reason` the submit-time gate stamps on its 422 envelope. */
export const PUBLISH_INTENT_REJECTED = 'publish_intent_rejected';

/**
 * Pull the typed problems out of a rejected `createPublishTask`, or null when
 * the failure was anything else.
 *
 * Deliberately strict about the envelope: FastAPI's own request-validation
 * 422 is ALSO a 422, but its `detail` is an array of pydantic errors with no
 * `reason`. Treating that as a gate verdict would print a made-up reason for
 * a completely different failure, so it falls through to null and the caller
 * shows its generic copy.
 */
export const publishGateProblems = (err: unknown): PublishGateProblem[] | null => {
  if (!(err instanceof DistributionApiError) || err.status !== 422) return null;
  const detail = err.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  const d = detail as { reason?: unknown; message?: unknown; problems?: unknown };
  if (d.reason !== PUBLISH_INTENT_REJECTED) return null;
  const problems = Array.isArray(d.problems)
    ? d.problems.filter(
      (p): p is PublishGateProblem =>
        Boolean(p) && typeof p === 'object' && typeof (p as PublishGateProblem).reason === 'string',
    )
    : [];
  // An envelope with the right reason but no usable problem list is still a
  // gate rejection — surface it as one unnamed problem rather than losing it.
  return problems.length > 0
    ? problems
    : [{ reason: PUBLISH_INTENT_REJECTED, message: String(d.message ?? '') }];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getApiUrl()}/api/v1/distribution${path}`, {
    ...init,
    headers: { ...(await getAuthHeaders()), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    // Best-effort: a body that isn't JSON (proxy HTML, empty 502) must not
    // turn a clean HTTP failure into a parse crash.
    let detail: unknown;
    try {
      const body = await res.json();
      detail = (body as { detail?: unknown })?.detail;
    } catch (parseErr) {
      console.error('distribution api: error body was not JSON', path, parseErr);
    }
    throw new DistributionApiError(res.status, path, detail);
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

/**
 * Start a QR-code (browser session) binding. Returns immediately with the
 * `task_tracking` row id — the scan itself runs in a DBOS workflow, and the
 * QR image plus every state change arrive over Supabase Realtime in
 * `task_tracking.metadata.login` (see `SessionLoginState`). There is no
 * response body to poll and no SSE channel.
 */
export const startSessionLogin = (body: {
  platform: string;
  scope_type: 'user' | 'team';
  scope_id: string;
}): Promise<{ task_id: string }> =>
  request<{ task_id: string }>('/accounts/session/login', {
    method: 'POST',
    body: JSON.stringify(body),
  });

/**
 * Typed result envelope the session endpoints return (backend
 * `SessionOpResponse`). A 200 with `success: false` is a real failure — the
 * platform rejected the input — so callers must branch on the flag rather
 * than on the HTTP status. `detail.error_kind` present means the failure was
 * infrastructural (browser container unreachable / unconfigured), which is a
 * different message to the user than "wrong code".
 *
 * `detail.code_rejected` is the SMS path's own marker: the code was typed into
 * the live page and the page is *still* asking for one. It rides on
 * `status: 'sms_required'` — the same value a first-time challenge uses — so
 * the status cannot carry that verdict and the flag has to.
 */
export interface SessionOpResult {
  success: boolean;
  status: string;
  message: string;
  detail?: { error_kind?: string; code_rejected?: boolean } & Record<string, unknown>;
}

/**
 * What the backend (and, verbatim, the browser container's `SmsCodeRequest`)
 * will accept. Enforced client-side too: anything else comes back as a
 * FastAPI 422 pydantic body, which is NOT a `SessionOpResult` and would land
 * the modal on `undefined.success`.
 */
export const SMS_CODE_PATTERN = /^\d{4,8}$/;

/** Answer the platform's SMS challenge (`status === 'sms_required'`). */
export const submitSmsCode = (taskId: string, code: string): Promise<SessionOpResult> =>
  request<SessionOpResult>(`/accounts/session/login/${encodeURIComponent(taskId)}/sms`, {
    method: 'POST',
    body: JSON.stringify({ code }),
  });

/**
 * What the backend's `SessionPhoneRequest` (and the browser container's
 * `PhoneNumberRequest`) accept, to the digit.
 *
 * Deliberately not "11 digits": that is the mainland mobile format, and baking
 * one country's shape into the client is the same species of assumption as
 * baking one platform's QR sign-in into every platform's modal.
 */
export const PHONE_NUMBER_PATTERN = /^\d{6,20}$/;

/**
 * Give the live sign-in page the account's phone number and ask the platform
 * to text a code (`status === 'phone_required'`).
 *
 * Only reachable on platforms whose capability says `login_method: 'sms'` —
 * they render no QR code, so this is the *first* step of the sign-in rather
 * than a fallback. A `success: false` here means the number went nowhere
 * (`detail.reason` is `phone_input_missing` or `code_request_failed`), and the
 * one thing the caller must not do with it is carry on to a code field: no
 * message was requested, so none is coming.
 */
export const submitLoginPhone = (
  taskId: string,
  phone: string,
): Promise<SessionOpResult> =>
  request<SessionOpResult>(`/accounts/session/login/${encodeURIComponent(taskId)}/phone`, {
    method: 'POST',
    body: JSON.stringify({ phone }),
  });

/**
 * Abandon a login. MUST be called when the user closes the modal mid-scan —
 * the browser container holds a live context per pending login, and without
 * this it spins until the server-side timeout.
 *
 * `context_released: false` means the task is cancelled but the headless-less
 * browser context outlives it until its TTL — worth a log, not worth blocking
 * the user's close.
 */
export const cancelSessionLogin = (
  taskId: string,
): Promise<{ cancelled: boolean; context_released: boolean; message: string }> =>
  request(`/accounts/session/login/${encodeURIComponent(taskId)}`, {
    method: 'DELETE',
  });

/**
 * Typed verdict on the browser service every QR binding runs inside (backend
 * `BrowserHealthResponse`). `ok: false` arrives as a **200**, not an error
 * status, on purpose: the caller has to tell "the browser service is down"
 * apart from "this request failed", and only the first one is evidence about
 * the browser. `error_kind` is `unreachable` / `timeout` / `not_configured` /
 * `server_error` / … — kept as a plain string so a kind added server-side
 * cannot break parsing.
 */
export interface BrowserHealth {
  ok: boolean;
  error_kind?: string | null;
  message?: string;
}

/**
 * Probe the QR-login channel before offering it. Read-only, and deliberately
 * NOT polled — the binding entry point asks once on mount, once before it
 * actually starts a login, and once more whenever the user hits Check again.
 *
 * A rejected promise here means our own API call failed, which says nothing
 * about the browser container; callers must not turn that into "unavailable".
 */
export const getBrowserHealth = (): Promise<BrowserHealth> =>
  request<BrowserHealth>('/browser/health');

/**
 * What one platform can publish today (backend `PlatformCapability`).
 *
 * This page used to carry its own copy of this table
 * (`components/Distribution/capabilities.ts`), kept in sync with the backend
 * profile by a comment saying the two must land in the same PR. That comment
 * did not stop the failure it was written for: the page offered an Images tab,
 * the backend profile allowed it, and the browser service — the only layer that
 * actually posts anything — refused with `unsupported_content_type` after the
 * user had filled in the whole form and waited in the queue.
 *
 * So the frontend now holds no capability constants at all. When the browser
 * service learns image posts, this response changes and the tab un-greys
 * itself — with no frontend release.
 *
 * `is_placeholder` marks a platform that can bind an account but cannot publish:
 * its backend profile carries guessed values that are deliberately blanked out
 * here rather than shipped as if they were measured facts.
 */
export interface PlatformCapability {
  platform: string;
  /**
   * How this platform is signed in: `'qrcode'` (a code is rendered, the user
   * scans it) or `'sms'` (no code exists — the user types a phone number and
   * the verification code the platform texts back).
   *
   * The one capability field that is **not** blanked out by `is_placeholder`,
   * because it is about binding rather than publishing, and a platform that can
   * only bind is exactly the one that needs it. Before this field existed the
   * modal drew a QR frame for every platform, including one whose creator site
   * has no scan sign-in at all — so the user watched a placeholder for an image
   * the backend could never produce.
   *
   * Typed as a plain string: a value added server-side must not break parsing,
   * and the modal falls back to a neutral shape for anything it does not know.
   */
  login_method: string;
  supports_publishing: boolean;
  is_placeholder: boolean;
  content_types: string[];
  video_extensions: string[];
  image_extensions: string[];
  min_images: number | null;
  max_images: number | null;
  max_title_len: number | null;
  max_topics: number | null;
  supports_scheduling: boolean;
  schedule_min_lead_seconds: number | null;
  schedule_max_ahead_seconds: number | null;
  self_declarations: string[];
  supports_collection: boolean;
  /** Whether the platform's editor has a music picker we can drive. False for
   *  every platform that cannot publish at all, so the Publish form can hide
   *  the field rather than offering something that would be refused. */
  supports_music: boolean;
}

/**
 * Per-platform capabilities, keyed by platform name.
 *
 * Pure constants server-side (no IO, no timeout, no degraded branch), so a
 * rejection here means our own API call failed. Callers must treat that as
 * "no evidence" and keep the safe default — which for image posts is *stay
 * disabled*, never "assume supported".
 */
export const getPlatformCapabilities = (): Promise<Record<string, PlatformCapability>> =>
  request<{ platforms: Record<string, PlatformCapability> }>('/capabilities')
    .then((r) => r.platforms);

/**
 * One live topic suggestion from the platform's own topic library.
 *
 * `topic_id` is the platform's topic ENTITY id (Douyin's challenge `cid`).
 * Empty means the platform has no entity for that word yet — which is exactly
 * what `is_new` says out loud, so no caller has to know what an empty string
 * is supposed to mean.
 *
 * `view_count` is the raw cumulative play count. Formatting is the UI's job
 * (locale-aware compact notation) — the API never sends a pre-formatted string.
 */
export interface TopicSuggestion {
  name: string;
  topic_id: string;
  view_count: number;
  is_new: boolean;
}

/**
 * Typed reasons a topic lookup can fail. The dropdown branches on these codes;
 * it never parses the English `message`.
 *
 * There is deliberately NO "empty list" failure mode: an empty `suggestions`
 * array always means the platform had nothing to suggest. Anything that went
 * wrong arrives as a rejection carrying one of these — otherwise "the API is
 * down" and "no such topic" would look identical in the dropdown.
 */
export const TOPIC_SUGGEST_REASONS = [
  'platform_unsupported',
  'keyword_empty',
  'upstream_unreachable',
  'upstream_status',
  'upstream_shape',
] as const;
export type TopicSuggestReason = (typeof TOPIC_SUGGEST_REASONS)[number];

/** Pull the typed reason out of a rejected `suggestTopics`, or null. */
export const topicSuggestReason = (err: unknown): TopicSuggestReason | null => {
  if (!(err instanceof DistributionApiError)) return null;
  const detail = err.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  const reason = (detail as { reason?: unknown }).reason;
  return TOPIC_SUGGEST_REASONS.includes(reason as TopicSuggestReason)
    ? (reason as TopicSuggestReason)
    : null;
};

/**
 * Ask the platform what topics start with `keyword` (type-ahead).
 *
 * Backed by the creator site's own suggest API, so the numbers are the real
 * ones the platform shows its own creators. Rejects (never resolves to `[]`)
 * when the lookup itself failed.
 */
export const suggestTopics = (
  keyword: string,
  platform = 'douyin',
  signal?: AbortSignal,
): Promise<TopicSuggestion[]> =>
  request<{ suggestions: TopicSuggestion[] }>(
    `/topics/suggest?platform=${encodeURIComponent(platform)}&keyword=${encodeURIComponent(keyword)}`,
    { signal },
  ).then((r) => r.suggestions ?? []);

export const refreshAccount = (id: string): Promise<SocialAccount> =>
  request<SocialAccount>(`/accounts/${id}/refresh`, { method: 'POST' });

/**
 * What unbinding this account would touch. Backing data for the unbind
 * confirmation, which must quote a measured number rather than an adjective —
 * the Remove button used to have no dialog at all while the backend cascaded
 * publish records off the account row.
 *
 * `publish_records` is now history the unbind KEEPS (soft delete, mig 416), and
 * `0` is a real answer that must be shown as 0, not hidden.
 */
export const getAccountUsage = (id: string): Promise<{ publish_records: number }> =>
  request<{ publish_records: number }>(`/accounts/${id}/usage`);

/**
 * Unbind an account. Soft since mig 416: the row and its publish history stay,
 * the stored credentials are destroyed, and re-scanning the QR code wakes the
 * same row. The HTTP shape (DELETE → 204) is unchanged.
 */
export const deleteAccount = (id: string): Promise<void> =>
  request<void>(`/accounts/${id}`, { method: 'DELETE' });

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

/**
 * Sample candidate cover frames from a video (asynchronous).
 *
 * Returns only a `task_id` — the frames need the source downloaded out of
 * object storage and N ffmpeg seeks, which is seconds to minutes, so it runs
 * as a DBOS workflow. The candidate list arrives over Supabase Realtime in
 * `task_tracking.metadata.cover_frames` (`CoverFramesMeta`), the same channel
 * the QR login uses. There is nothing to poll here.
 *
 * The fail-fast validation (source missing / not a video / not yours) happens
 * before the workflow starts, so those come back as a 4xx from THIS call
 * rather than as a red task minutes later.
 */
export const extractCoverFrames = (body: {
  resource_id: string;
  num_frames?: number;
}): Promise<{ task_id: string }> =>
  request<{ task_id: string }>('/covers/extract', {
    method: 'POST',
    body: JSON.stringify(body),
  });

/**
 * Turn one picked candidate frame into the vertical 3:4 + horizontal 4:3 pair
 * (synchronous — it only centre-crops an image that already exists).
 *
 * `publish_task_id` is for editing a task that already exists; the compose
 * form picks a cover BEFORE the task is created and passes the two returned
 * ids into the create body instead.
 */
export const selectCoverFrame = (body: {
  frame_resource_id: string;
  publish_task_id?: string;
}): Promise<CoverSelectResult> =>
  request<CoverSelectResult>('/covers/select', {
    method: 'POST',
    body: JSON.stringify(body),
  });

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
    // The backend projects the WHOLE resources row (`row_to_json(r.*)`), so
    // these have always been on the wire — the picker just never read them.
    // `unknown` here (not `number`) because this is a raw HTTP response body:
    // shapes get asserted at the mapper, never assumed at the boundary.
    duration_seconds?: unknown;
    resolution?: unknown;
    file_size_bytes?: unknown;
    created_at?: unknown;
  } | null;
}

/** Narrow a JSON number field, rejecting the shapes that would render as
 *  nonsense. Returns `null` (→ an em dash in the UI) rather than a guess. */
const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

/** Narrow a JSON string field, treating `''` as absent. */
const str = (v: unknown): string | null =>
  typeof v === 'string' && v !== '' ? v : null;

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
        duration_seconds: num(r.resource?.duration_seconds),
        resolution: str(r.resource?.resolution),
        file_size_bytes: num(r.resource?.file_size_bytes),
        // The resource's own created_at, not the top-level one — that is the
        // `resource_items` join row's timestamp (when it was filed into this
        // folder), which is not what "when was this video made" means.
        created_at: str(r.resource?.created_at),
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
