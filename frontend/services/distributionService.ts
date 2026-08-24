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

/** The `detail.reason` a retry gets when the batch's schedule can no longer be
 *  honoured. See `isScheduleUnreachable`. */
export const SCHEDULE_UNREACHABLE = 'schedule_unreachable';

/**
 * Was this retry refused because the batch's scheduled time is gone?
 *
 * The page normally never asks — it reads `schedule_state` off the task and
 * shows "Publish now" instead of "Retry" in the first place. But that field is
 * a snapshot: a records page left open across the deadline still renders
 * Retry, and pressing it lands on this 409. Falling back to a generic "Retry
 * failed" there would take a reason the backend deliberately typed and hand
 * the user nothing — the same silence this whole change is about.
 */
export const isScheduleUnreachable = (err: unknown): boolean => {
  if (!(err instanceof DistributionApiError) || err.status !== 409) return false;
  const detail = err.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return false;
  return (detail as { reason?: unknown }).reason === SCHEDULE_UNREACHABLE;
};

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
 * How long WE take to confirm a publish went live (`publish_readback`).
 *
 * ⚠️ These are our numbers, not the platform's — deliberately a separate shape
 * from `PlatformCapability` so nobody reads them as a platform promise. They
 * ride the capabilities endpoint only because that is already the channel for
 * "the backend owns this constant, the frontend just displays it".
 */
export interface ReadbackTiming {
  /** Earliest we look at all, measured from the moment the post went out. */
  first_check_after_seconds: number;
  /** After this we stop asking and the work item is blocked as unconfirmed. */
  give_up_after_seconds: number;
}

export const getReadbackTiming = (): Promise<ReadbackTiming | null> =>
  request<{ publish_readback?: ReadbackTiming | null }>('/capabilities')
    .then((r) => r.publish_readback ?? null);

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

/**
 * One track from the platform's own catalogue.
 *
 * `music_id` is the platform's `id_str` — a STRING, deliberately. The same
 * upstream row also carries an `id` as a JSON number past 2^53 (measured:
 * 6953836671917951012), which is already a different number by the time it
 * reaches this file. Publishing with the wrong id publishes the wrong song,
 * and a published post cannot swap its music.
 *
 * `duration` is seconds and `user_count` is the raw integer: formatting
 * (`5:25`, `3万人使用`) is the UI's job, never the API's.
 *
 * ⚠️ `user_count` is `number | null`. The SEARCH endpoint always fills it, but
 * the CHART cache carries the platform's own three-state answer: `0` is a real
 * catalogue value (a track used by nobody — one such track produced the
 * 2026-08-17 publish refusal) and `null` means the payload did not say. A UI
 * that renders `null` as `0` invents a measurement.
 */
export interface MusicTrack {
  music_id: string;
  title: string;
  author: string;
  duration: number;
  user_count: number | null;
  cover_url: string;
  play_url: string;
}

/**
 * Whose session the catalogue was read with.
 *
 * The panel MUST show this. The catalogue itself is not account-specific, but
 * the search credential is minted with one account's cookies, and the
 * favourites tab is account-scoped on the platform (measured: three accounts,
 * 0 / 18 / 9 saved tracks). Without it, changing the target account silently
 * changes the list — the system knowing something the user cannot see.
 */
export interface MusicBrowseIdentity {
  account_id: string;
  username: string;
  avatar_url: string | null;
}

export interface MusicSearchPage {
  tracks: MusicTrack[];
  cursor: number;
  has_more: boolean;
  cached: boolean;
  browsing_as: MusicBrowseIdentity;
}

/**
 * Typed reasons a catalogue lookup can fail. The panel branches on these
 * codes; it never parses the English `message`.
 *
 * There is deliberately NO "empty list" failure mode. An empty `tracks` array
 * means the platform answered with nothing — which is rare enough to be
 * suspicious (a nonsense keyword still returns ~8 fuzzy matches), so the copy
 * for it says "the platform returned nothing for that", not "no results".
 */
export const MUSIC_SEARCH_REASONS = [
  'platform_unsupported',
  'keyword_empty',
  'account_not_session_bound',
  'session_unusable',
  'signature_unavailable',
  'upstream_unreachable',
  'upstream_status',
  'upstream_shape',
] as const;
export type MusicSearchReason = (typeof MUSIC_SEARCH_REASONS)[number];

/** Pull the typed reason (and any back-off hint) out of a rejected search. */
export const musicSearchFailure = (
  err: unknown,
): { reason: MusicSearchReason | null; retryAfterS: number | null } => {
  if (!(err instanceof DistributionApiError)) return { reason: null, retryAfterS: null };
  const detail = err.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return { reason: null, retryAfterS: null };
  }
  const raw = (detail as { reason?: unknown }).reason;
  const after = (detail as { retry_after_s?: unknown }).retry_after_s;
  return {
    reason: MUSIC_SEARCH_REASONS.includes(raw as MusicSearchReason)
      ? (raw as MusicSearchReason)
      : null,
    retryAfterS: typeof after === 'number' ? after : null,
  };
};

/**
 * Search the platform's music catalogue.
 *
 * `accountId` is whose session pays for the lookup (the first target account).
 * Rejects — never resolves to an empty page — when the lookup itself failed.
 */
export const searchMusic = (
  keyword: string,
  accountId: string,
  cursor = 0,
  platform = 'douyin',
  signal?: AbortSignal,
): Promise<MusicSearchPage> =>
  request<MusicSearchPage>(
    `/music/search?platform=${encodeURIComponent(platform)}`
    + `&account_id=${encodeURIComponent(accountId)}`
    + `&keyword=${encodeURIComponent(keyword)}`
    + `&cursor=${cursor}`,
    { signal },
  );

/**
 * One cached chart tab of the platform's 「选择音乐」 panel.
 *
 * ⚠️ `category_id` alone is NOT the identity. Measured on the live panel:
 * 推荐 and 收藏 both answer `"1"` and differ only by `kind`, so anything that
 * keys a tab (React list keys included) must use both.
 *
 * ⚠️ `ok === true` with `tracks: []` is a real, common state — an account with
 * no saved tracks. `ok === false` is "we failed to read this tab", and it
 * still carries whatever tracks survived from the previous harvest. Rendering
 * the two the same way either shows an empty tab as if the platform had
 * nothing, or shows a stale tab as if it were fresh.
 */
export interface MusicChart {
  id: string;
  category_id: string;
  category_kind: string;
  category_name: string;
  position: number;
  ok: boolean;
  error: string;
  cursor: string;
  has_more: boolean;
  /** When the tracks below were read. Advances on SUCCESS only. */
  fetched_at: string | null;
  /** When we last tried. Advances every attempt — including failures. */
  checked_at: string | null;
  tracks: MusicTrack[];
}

/**
 * The cached charts, plus three separate facts about freshness.
 *
 * `never_harvested` is NOT `stale` with a different name: a cold cache needs
 * "nothing has been read yet, want to read it now?", a stale one needs
 * "this is from yesterday". Only one of them is worth a warning, and merging
 * them would make the empty first-run look like a fault.
 */
export interface MusicChartsPage {
  charts: MusicChart[];
  last_success_at: string | null;
  stale: boolean;
  never_harvested: boolean;
  ttl_hours: number;
}

/** The tab identity. Both halves, always — see `MusicChart`. */
export const musicChartKey = (chart: {
  category_kind: string;
  category_id: string;
}): string => `${chart.category_kind}:${chart.category_id}`;

/**
 * Read this account's cached chart tabs. **Cheap** — never opens a browser.
 */
export const fetchMusicCharts = (
  accountId: string,
  signal?: AbortSignal,
): Promise<MusicChartsPage> =>
  request<MusicChartsPage>(`/accounts/${encodeURIComponent(accountId)}/music/charts`, {
    signal,
  });

/**
 * Harvest this account's charts from the platform now.
 *
 * ⚠️ **Expensive, and it leaves a draft.** Reaching the platform's music panel
 * requires an upload, so a refresh costs ~2 minutes of browser time and one
 * draft on the account. Any caller must say so before the user clicks; this is
 * not a button to fire on mount.
 */
export const refreshMusicCharts = (accountId: string): Promise<unknown> =>
  request<unknown>(`/accounts/${encodeURIComponent(accountId)}/music/charts/refresh`, {
    method: 'POST',
  });

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

/**
 * Re-dispatch a failed batch.
 *
 * `mode` is the whole point of this signature. A batch keeps the
 * `scheduled_at` it was created with, so retrying one whose time has passed is
 * rejected again the moment the browser opens — the backend now answers that
 * with a typed 409 (`reason: 'schedule_unreachable'`) instead of a cheerful
 * 200 that changes nothing.
 *
 * `'now'` is the escape hatch: it drops the schedule and publishes
 * immediately. That is a DIFFERENT intent from "try again", and irreversible
 * once the post is up, so it is never the default and never inferred — the
 * caller says it because the user pressed a button that says it.
 *
 * `dropMusic` is the same kind of escape hatch on a second axis: it clears the
 * batch's track so the retry never opens the platform's music dialog. A batch
 * that failed on music keeps the track it was created with and the records
 * page cannot edit it, so a plain retry runs the identical search. Orthogonal
 * to `mode` on purpose — a batch can have BOTH an expired schedule and a track
 * that will not select, and "publish now, without music" is then the only
 * combination that can succeed.
 */
export const retryPublishTask = (
  id: string,
  mode: 'as_scheduled' | 'now' = 'as_scheduled',
  options: { dropMusic?: boolean } = {},
): Promise<PublishTask> =>
  request<PublishTask>(`/tasks/${id}/retry`, {
    method: 'POST',
    // Both flags always on the wire rather than leaning on the server default:
    // `drop_music: false` is the value that means "keep my track", and an
    // omitted key cannot be told apart from a client that predates the field.
    body: JSON.stringify({ mode, drop_music: Boolean(options.dropMusic) }),
  });

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
 * Turn the picked frame into the vertical 3:4 + horizontal 4:3 pair
 * (synchronous).
 *
 * What travels is a COORDINATE, not an id: candidate frames are never
 * persisted, so the server goes back to the source video and re-reads the
 * frame at `timestamp_seconds` before cropping. That is the same ffmpeg seek
 * that produced the preview the user clicked, so the picked frame and the
 * cropped frame are the same one.
 *
 * `publish_task_id` is for editing a task that already exists; the compose
 * form picks a cover BEFORE the task is created and passes the two returned
 * ids into the create body instead.
 */
/**
 * Grab one frame by hand, to use as a REFERENCE (Cover Studio).
 *
 * Not `selectCoverFrame`: that one finishes a cover (crops 3:4 + 4:3 into two
 * `resources` rows that the publish task references by id). This one only
 * collects raw material and lands it as a single `generated_media` row —
 * because the image generation bridge accepts ONLY
 * `/api/v1/generated-media/{id}/...` URLs as references and silently drops
 * anything else. Pass `url` through untouched; rebuilding it from `id` is the
 * one place the two could drift apart.
 */
export interface GrabbedCoverFrame {
  generated_media_id: string;
  url: string;
  timestamp_seconds: number;
}

export const grabCoverFrame = async (
  sourceResourceId: string,
  timestampSeconds: number,
): Promise<GrabbedCoverFrame> =>
  request<GrabbedCoverFrame>('/covers/grab-frame', {
    method: 'POST',
    body: JSON.stringify({
      source_resource_id: sourceResourceId,
      timestamp_seconds: timestampSeconds,
    }),
  });

export const selectCoverFrame = (body: {
  source_resource_id: string;
  timestamp_seconds: number;
  publish_task_id?: string;
} | {
  /** DEPRECATED shape, for the deploy-skew window only — a backend that has
   *  not shipped yet still persists candidate frames and only understands an
   *  id. See `CoverCandidate.resource_id`. */
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

/**
 * The same query as ``listLibraryMedia`` but it THROWS instead of swallowing.
 *
 * ``listLibraryMedia`` catches everything and returns ``[]``, which makes a
 * failed load indistinguishable from "you own no media" — the publish picker
 * tolerates that because an empty grid there still reads as "nothing to pick".
 * A surface that must tell the two apart (the cover-template picker says
 * "Nothing here yet" in one case and "Could not load your library" in the
 * other) cannot be built on a function whose only failure signal is an empty
 * array. New callers should prefer this one.
 */
export const listLibraryMediaOrThrow = async (
  scopeId: string,
  opts?: { tagId?: string; mediaType?: 'video' | 'image' },
): Promise<LibraryVideo[]> => {
  {
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
  }
};

export const listLibraryMedia = async (
  scopeId: string,
  opts?: { tagId?: string; mediaType?: 'video' | 'image' },
): Promise<LibraryVideo[]> => {
  try {
    return await listLibraryMediaOrThrow(scopeId, opts);
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

// ── 发布中途的短信验证码通道 ──────────────────────────────────
//
// 与登录侧的 `submitSmsCode` 是两条独立的路径,不要合并:登录时浏览器会话由
// `/start` 交回一个 id,发布则在结束前什么都不返回,id 由后端自己生成并写进
// task_tracking。前端因此按 **publish task id** 寻址,而不是按会话 id。

/**
 * "这次发布此刻在不在等验证码"。
 *
 * `waiting` 语义很窄:它表示**有一个发布协程此刻正停在 await 上**,不是"页面上
 * 看见了验证码输入框"。放宽它就会给一个没人在等的用户弹输入框。
 */
export interface PublishSmsState {
  waiting: boolean;
  account_id?: number | null;
  platform?: string | null;
  attempts_left: number;
  max_attempts: number;
  seconds_remaining: number;
  outcome?: string | null;
  message: string;
}

/**
 * 平台对提交的码做了什么。**闭集**(浏览器侧 `publish_sms.py` 定义):
 * `accepted` / `rejected` / `exhausted` / `expired` / `abandoned` /
 * `not_pending` / `unreachable`。
 *
 * 用类型化 outcome 而不是布尔成功位,是因为 `rejected`(码不对,重输)和
 * `unreachable`(没送到,等一下再试)要用户做的事正相反。
 *
 * `retryable` 由浏览器算好透传,前端**不重新推导** —— 重推一次就是多一次和浏览器
 * 分歧的机会,而分歧的形态会是"界面说还能再试、发布其实已经放弃了"。
 */
export interface PublishSmsVerdict {
  outcome: string;
  message: string;
  attempts_left: number;
  retryable: boolean;
}

/** 轮询某个发布批次是否卡在验证码上。 */
export const getPublishSmsState = (taskId: number | string): Promise<PublishSmsState> =>
  request<PublishSmsState>(`/tasks/${encodeURIComponent(String(taskId))}/sms`);

/** 把验证码交给那个正在等它的发布,并当场拿回平台的裁决。 */
export const submitPublishSmsCode = (
  taskId: number | string,
  code: string,
): Promise<PublishSmsVerdict> =>
  request<PublishSmsVerdict>(`/tasks/${encodeURIComponent(String(taskId))}/sms`, {
    method: 'POST',
    body: JSON.stringify({ code }),
  });
