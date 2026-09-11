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

/** One registered version of one object. */
export interface OutputVersion {
  id: string;
  version: number;
  parent_version: number | null;
  run_id: string;
  issue_id: string | null;
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
  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = 'OutputsError';
    this.code = code;
    this.status = status;
  }
}

async function reject(res: Response): Promise<never> {
  let code = `http_${res.status}`;
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown; details?: unknown; error?: unknown };
    const detail = body?.details ?? body?.detail;
    if (detail && typeof detail === 'object') {
      const d = detail as { code?: unknown; message?: unknown };
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
  throw new OutputsError(code, res.status, message);
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

/** One object's whole chain, newest first. Throws `not_registered` when the
 *  object was never registered — an empty list would read as "no versions". */
export async function getOutputLineage(kind: string, refId: string): Promise<OutputLineage> {
  return get<OutputLineage>(`/api/v1/outputs/${encodeURIComponent(kind)}/${ref(refId)}`);
}

/** Two versions as content. `from`/`to` are the wire's own query names. */
export async function getOutputDiff(kind: string, refId: string, from: number, to: number): Promise<OutputDiff> {
  return get<OutputDiff>(`/api/v1/outputs/${encodeURIComponent(kind)}/${ref(refId)}/diff?from=${from}&to=${to}`);
}
