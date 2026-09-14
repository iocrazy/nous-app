/**
 * 产出血缘 client (harness 3a §4/§5) — the three endpoints Task 3 serves:
 *
 *   GET /api/v1/issues/{id}/outputs           every object this issue produced
 *   GET /api/v1/outputs/{kind}/{ref_id}       one object's version chain
 *   GET /api/v1/outputs/{kind}/{ref_id}/diff  two of those versions, as content
 *
 * **Every id is a string.** `run_deliverables.id`, `run_id`, `issue_id` and
 * `ref_id` are Snowflake BIGINTs; the backend stringifies them on purpose and
 * nothing here may turn one back into a number (CLAUDE.md「Snowflake BIGINT
 * 精度丢失」).
 *
 * **A refusal is read from `details.code`.** Production wraps every
 * HTTPException in the ErrorResponse envelope, so the typed code lives under
 * `details`, not `detail` — reading only the latter turns `not_registered`
 * into `http_404` on the real stack while every unit test stays green
 * (CLAUDE.md 2026-09-09).
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

/**
 * One registered version of one object.
 *
 * `issue_key` and `deep_link` are what makes the issue REACHABLE. `issue_id`
 * alone cannot address it — the route is `/team/{team_id}/todolist/{key}`, so
 * a URL built from the snowflake 404s or lands on an unrelated issue. The
 * backend therefore hands over a finished link (`?step=` appended when the
 * step is known) or `null`, and no frontend assembles one. Both are `null`
 * for a run that answers to no issue, or an issue with no key or no team.
 *
 * Both are REQUIRED, not optional: 3a Task 3b puts them on every version of
 * both list endpoints, `null` included, so a fixture that omits them is
 * describing a response the backend does not send.
 */
export interface OutputVersion {
  id: string;
  version: number;
  parent_version: number | null;
  /** The run that wrote it — `null` for a version a PERSON registered (3b: a
   *  revert, and the edits it kept). `run_id` and `actor_user_id` are the two
   *  possible authors and the T1 CHECK keeps at least one of them non-null. */
  run_id: string | null;
  /** 谁写的，当它不是 run。人手登记（回退）才有值 —— run_id 与它至少有一个非空（T1 CHECK）。 */
  actor_user_id: string | null;
  /** 这一版回退自哪一版；非回退为 null。 */
  reverted_from_version: number | null;
  /** 花费怎么来的：allocated = 从 step 花费均摊（文本类，参考值），exact = 登记时的目录价。 */
  cost_kind: 'allocated' | 'exact' | null;
  issue_id: string | null;
  issue_key: string | null;
  deep_link: string | null;
  seq: number | null;
  turn: number | null;
  step: number | null;
  title: string | null;
  model: string | null;
  cost_cents: number | null;
  created_at: string | null;
}

/** Every version of ONE object — the panel's unit is the object, not the row. */
export interface OutputObject {
  kind: string;
  ref_id: string;
  title: string | null;
  latest_version: number;
  /** Newest first, as the endpoint orders them. */
  versions: OutputVersion[];
}

export interface OutputLineage {
  kind: string;
  ref_id: string;
  latest_version: number;
  versions: OutputVersion[];
  /**
   * The registry sequence this answer was read at — a Snowflake id, and so a
   * STRING. Never parse it into a number: past 2^53 that silently rounds
   * (CLAUDE.md「Snowflake BIGINT 精度丢失」). Compare two of them with
   * `BigInt(a) < BigInt(b)` if you ever need to order them.
   */
  as_of_seq: string;
}

export interface OutputDiffMedia {
  id: string;
  media_kind: string | null;
  mime: string | null;
  cover_url: string | null;
  stream_url: string | null;
}

/**
 * One side of a diff. `available: false` is a fact of its own: the snapshot
 * could not be reconstructed (`no_snapshot` / `no_ledger` / `not_found`), which
 * is NOT the same as a version whose text is empty — the dialog must say which.
 */
export interface OutputDiffSide {
  version: number;
  run_id: string;
  issue_id: string | null;
  created_at: string | null;
  model: string | null;
  cost_cents: number | null;
  title: string | null;
  text: string | null;
  media: OutputDiffMedia | null;
  available: boolean;
  unavailable_reason: string | null;
}

export interface OutputDiff {
  kind: string;
  ref_id: string;
  content_type: 'text' | 'media';
  from: OutputDiffSide;
  to: OutputDiffSide;
}

/**
 * Make a backend media URL usable in an `<img>` / `<video>`.
 *
 * The backend builds these RELATIVE (`/api/v1/generated-media/{id}/cover`,
 * see `app/services/deliverables/diff.py`), and the app is served from a
 * different origin than the API — on Cloudflare Pages `public/_redirects`
 * sends `/*` to `index.html`, so a bare `/api/...` comes back as HTML and the
 * image silently fails to load. Prefixing is the FRONTEND's job (same rule
 * `getResourceCoverUrl` follows), and it happens here only: the card
 * thumbnail and the dialog both call this, so there is one place to be right.
 *
 * Anything already absolute — `https:`, `data:`, `blob:`, protocol-relative —
 * passes through untouched.
 */
export function resolveMediaUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(url)) return url;
  const base = getApiUrl().replace(/\/+$/, '');
  return url.startsWith('/') ? `${base}${url}` : `${base}/${url}`;
}

/** A refusal the caller can BRANCH on. Mirrors ScheduleRejectedError. */
export class OutputsError extends Error {
  readonly code: string;
  readonly status: number;
  /**
   * The whole typed payload the server sent under `details`, not just its
   * `code`. A refusal often carries the fact the copy has to name — the
   * version that appeared (`latest_version`), why a snapshot is gone
   * (`reason`) — and dropping it leaves the caller able to say only "there
   * was a conflict", which does not tell the reader who wrote what.
   */
  readonly details: Record<string, unknown> | null;
  constructor(code: string, status: number, message: string, details: Record<string, unknown> | null = null) {
    super(message);
    this.name = 'OutputsError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function reject(res: Response): Promise<never> {
  let code = `http_${res.status}`;
  let message = `${res.status} ${res.statusText}`;
  let details: Record<string, unknown> | null = null;
  try {
    const body = (await res.json()) as { detail?: unknown; details?: unknown; error?: unknown };
    const detail = body?.details ?? body?.detail;
    if (detail && typeof detail === 'object') {
      const d = detail as { code?: unknown; message?: unknown };
      // The WHOLE object, not the two keys we happen to read here: a refusal's
      // extra facts (`latest_version`, `reason`) are what the caller's copy
      // names, and this parser must not decide which of them matter.
      details = detail as Record<string, unknown>;
      if (typeof d.code === 'string' && d.code) code = d.code;
      if (typeof d.message === 'string' && d.message) message = d.message;
    } else if (typeof detail === 'string' && detail) {
      message = detail;
    } else if (typeof body?.error === 'string' && body.error) {
      message = body.error;
    }
  } catch (err) {
    // Not JSON at all (a gateway's HTML) — keep the status line.
    console.error('[outputsService] error body was not JSON', err);
  }
  throw new OutputsError(code, res.status, message, details);
}

async function get<T>(path: string): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}${path}`, { headers });
  if (!res.ok) return reject(res);
  return (await res.json()) as T;
}

/** Everything this issue's runs registered, grouped by object (first-seen order). */
export async function listIssueOutputs(issueId: number | string): Promise<OutputObject[]> {
  const body = await get<{ items?: OutputObject[] }>(`/api/v1/issues/${issueId}/outputs`);
  return body.items ?? [];
}

const ref = (refId: string): string => encodeURIComponent(refId);

/**
 * One object's lineage, in flight or already answered.
 *
 * ⚠️ **Module-level, and deliberately so.** `OutputProvenance` mounts once per
 * OBJECT — one per shot on a canvas, one per scene in a script sheet — and
 * nothing above it knows about its siblings. Without sharing here, opening a
 * 40-shot canvas fires 40 requests, nearly all of which come back 404
 * `not_registered` because most objects were written by a person; and
 * `EditorShell` keys the scene subtree on `rollbackNonce`, so a single
 * rollback remounts every block and fires the whole set again.
 *
 * Holding the PROMISE rather than the value is what makes the concurrent case
 * work: forty components mounting in one tick all await the same request
 * instead of racing to start forty.
 *
 * **Lifetime is the page load.** There is no scope to invalidate against — the
 * block hangs off canvas nodes and scene blocks, not off an issue or a route —
 * so nothing clears this on navigation. That is sound because a registered
 * lineage only grows when an AGENT writes, which cannot happen inside this
 * tab; the two paths that can make it stale call `invalidateOutputLineage`.
 */
const lineageCache = new Map<string, Promise<OutputLineage>>();

const lineageKey = (kind: string, refId: string): string => `${kind}:${refId}`;

/**
 * Forget one object, so the next read asks again.
 *
 * ONE object, never the whole map: a revert touches a single scene, and
 * dropping everything would turn that into exactly the 60-request reload this
 * cache exists to prevent.
 *
 * Call it wherever an object's registered chain can change under us — a
 * successful revert (3b), a fresh registration this tab caused. A rollback in
 * the script editor is NOT one of those: it writes new ops as the USER, and
 * `run_deliverables` only records agent writes, so the chain is unchanged and
 * the remount it triggers should cost nothing.
 */
export function invalidateOutputLineage(kind: string, refId: string): void {
  lineageCache.delete(lineageKey(kind, refId));
}

/** Drop every cached lineage. For tests, which need each case to reach the
 *  transport; production invalidates one object at a time. */
export function clearOutputLineageCache(): void {
  lineageCache.clear();
}

/** One object's whole chain, newest first. Throws `not_registered` when the
 *  object was never registered — an empty list would read as "no versions".
 *
 *  Shared through {@link lineageCache}: concurrent callers get one request,
 *  and a settled answer is reused for the life of the page.
 *
 *  **`not_registered` is cached like an answer**, because it IS one — "a
 *  person made this" is a fact about the object and the common case, so
 *  re-asking it on every remount is the storm the cache exists to stop.
 *  Every OTHER failure is evicted on settle: a 502 is a fact about the last
 *  few seconds, not about the object, and freezing it would leave "could not
 *  read where this came from" on screen until the user navigated away. */
export function getOutputLineage(kind: string, refId: string): Promise<OutputLineage> {
  const key = lineageKey(kind, refId);
  const cached = lineageCache.get(key);
  if (cached) return cached;

  const pending = get<OutputLineage>(
    `/api/v1/outputs/${encodeURIComponent(kind)}/${ref(refId)}`,
  ).catch((err: unknown) => {
    // Evict everything except the registry's own "nothing produced this".
    // Note this runs BEFORE the caller's own handler, so a later read sees an
    // already-cleared slot and retries — which is the point.
    if (!(err instanceof OutputsError) || err.code !== 'not_registered') {
      lineageCache.delete(key);
    }
    throw err;
  });

  lineageCache.set(key, pending);
  return pending;
}

/** Two versions as content. `from`/`to` are the wire's own query names. */
export async function getOutputDiff(kind: string, refId: string, from: number, to: number): Promise<OutputDiff> {
  return get<OutputDiff>(`/api/v1/outputs/${encodeURIComponent(kind)}/${ref(refId)}/diff?from=${from}&to=${to}`);
}

/** What a revert wrote: the new version, and the one the backend had to
 *  register first if this tab's object held unregistered human edits. */
export interface RevertResult {
  version: OutputVersion;
  /** 回退前把未登记的人手编辑登记成的那一版；没有就是 null。
   *  弹层调用前无从得知它会不会出现，所以确认文案不提它，成功后才说。 */
  kept_version: OutputVersion | null;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}${path}`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) return reject(res);
  return (await res.json()) as T;
}

/** 把对象回退到 `toVersion`，写出新的一版。`expectedLatest` 是乐观锁：与服务端当前
 *  最新版不等就 409 `version_conflict`（detail 带 `latest_version`），绝不覆盖别人刚登记的版本。 */
export async function revertOutput(
  kind: string,
  refId: string,
  opts: { toVersion: number; expectedLatest: number },
): Promise<RevertResult> {
  return post<RevertResult>(
    `/api/v1/outputs/${encodeURIComponent(kind)}/${ref(refId)}/revert`,
    { to_version: opts.toVersion, expected_latest: opts.expectedLatest },
  );
}
