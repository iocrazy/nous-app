// frontend/services/apiEnvelope.ts
// Shared reader for the `{success, data}` / `{success:false, error:{...}}`
// envelope that `backend/app/api/assets_router.py` (`_ok` / `_err`) emits and
// `generated_router.py` imports rather than re-implements. Both frontend
// clients read it through here for the same reason: one shape of failure, in
// one place, so /assets and /generated cannot drift into reporting the same
// refusal two different ways.

import { reportApiNetworkFailure } from '../utils/apiConfig';

/**
 * A typed refusal from /assets or /generated.
 *
 * `code` is the backend's machine-readable reason (`not_a_member`,
 * `asset_exists`, `invalid_slot`, …). When the body is NOT an envelope — a
 * proxy 502, a FastAPI validation `{"detail": ...}`, an HTML error page — the
 * code is synthesized as `http_<status>` so callers always have a branchable
 * value and never have to inspect `undefined`.
 *
 * `extra` carries whatever else the error object held (`existing_asset_id` on
 * the 409). Dropping it would turn a recoverable collision into a dead end.
 */
export class GeneratedApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly extra: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    detail: string,
    extra: Record<string, unknown> = {},
  ) {
    super(`${code}: ${detail} (HTTP ${status})`);
    this.name = 'GeneratedApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.extra = extra;
  }
}

/** `status` for a request that never reached the server — there is no HTTP
 *  status to report, and 0 is distinguishable from every real one. */
export const NETWORK_STATUS = 0;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Build the error for a non-2xx response, from the envelope when it is one. */
function errorFromBody(status: number, body: unknown): GeneratedApiError {
  if (isRecord(body) && isRecord(body.error) && typeof body.error.code === 'string') {
    const { code, detail, ...extra } = body.error;
    return new GeneratedApiError(
      status,
      code,
      typeof detail === 'string' ? detail : `HTTP ${status}`,
      extra,
    );
  }
  // Not our envelope. Keep whatever human-readable string is on offer (FastAPI
  // 422s answer `{"detail": ...}`) but do not pretend it came with a code.
  const detail =
    isRecord(body) && typeof body.detail === 'string' ? body.detail : `HTTP ${status}`;
  return new GeneratedApiError(status, `http_${status}`, detail);
}

/**
 * Unwrap `data` from an envelope response, or throw {@link GeneratedApiError}.
 *
 * Throws — rather than returning a result union — so a caller that forgets to
 * branch gets a loud rejection instead of a silent no-op (CLAUDE.md
 * "触发路径必须类型化失败回显").
 */
export async function unwrapEnvelope<T>(res: Response): Promise<T> {
  let body: unknown = null;
  let unreadable = false;
  try {
    body = await res.json();
  } catch (err) {
    unreadable = true;
    if (res.ok) console.error('[apiEnvelope] 2xx response with an unreadable body:', err);
  }

  if (!res.ok) throw errorFromBody(res.status, unreadable ? null : body);

  // A 2xx that says `success:false` is not a contract we emit, but reading it
  // as success would hand the caller `undefined` as data.
  if (isRecord(body) && body.success === false) throw errorFromBody(res.status, body);
  if (unreadable) {
    // A 2xx we cannot read is not a success: returning `undefined` as `data`
    // here would surface later as a blank list that looks like an empty one.
    throw new GeneratedApiError(res.status, 'unreadable_body', 'Unreadable response body');
  }
  return (isRecord(body) ? body.data : undefined) as T;
}

/**
 * `fetch` + {@link unwrapEnvelope}, with fetch-level failures normalized into
 * the same error type. Without this the caller would have to tell a
 * `TypeError: Failed to fetch` apart from a bug in its own code.
 */
export async function envelopeFetch<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err) {
    console.error('[apiEnvelope] request never reached the server:', url, err);
    // Feed the dual-channel failover. `reportApiNetworkFailure` counts ONLY
    // fetch-level failures (two consecutive ones flip the app to the
    // Cloudflare fallback); an HTTP error is the origin answering, so it must
    // NOT be reported here. Skipping this call would leave these two clients
    // as the one pair of surfaces that keeps hammering a dead primary while
    // the rest of the app has already failed over.
    reportApiNetworkFailure();
    throw new GeneratedApiError(
      NETWORK_STATUS,
      'network',
      'The request never reached the server',
    );
  }
  return unwrapEnvelope<T>(res);
}

/**
 * Auth headers + `Content-Type: application/json`, as a real `Headers`.
 *
 * NOT `{'Content-Type': ..., ...auth}`: `parserService.getAuthHeaders` is typed
 * `Promise<HeadersInit>`, which admits a `Headers` instance and a `string[][]`
 * as well as a plain object — and spreading either of those yields `{}`, i.e.
 * every request silently loses its Authorization and 401s. `new Headers(...)`
 * normalizes all three shapes. TypeScript does not catch the spread, so this
 * is pinned by test instead.
 */
export function jsonHeaders(auth: HeadersInit): Headers {
  const headers = new Headers(auth);
  headers.set('Content-Type', 'application/json');
  return headers;
}
