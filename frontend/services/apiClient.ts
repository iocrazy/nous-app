/**
 * Unified API client for the MediaHub frontend.
 *
 * Wraps fetch with:
 *   - Automatic Supabase access token injection (fresh via getSession)
 *   - Optional X-API-Key fallback from localStorage
 *   - Selected team id header
 *   - JSON-aware response parsing with unified ErrorResponse handling
 *   - Consistent surface for GET / POST / PATCH / DELETE
 *
 * Services should migrate to `apiFetch` / `apiJson` instead of hand-rolling
 * headers in each file. The 28+ per-service getAuthHeaders implementations
 * are duplicated boilerplate and drift over time.
 */

import { getSupabaseAccessToken } from '../supabaseClient';
import { getApiUrl } from '../utils/apiConfig';

const API_KEY_STORAGE_KEYS = ['mediahub_api_key', 'douyin_api_key'] as const;
const TEAM_STORAGE_KEY = 'mediahub_selected_team';

function readStorage(key: string): string | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage.getItem(key);
    }
  } catch (err) {
    console.debug('localStorage read failed:', err);
  }
  return null;
}

function getApiKey(): string | null {
  for (const key of API_KEY_STORAGE_KEYS) {
    const value = readStorage(key);
    if (value) return value;
  }
  return null;
}

/**
 * Build auth headers. Prefers API key if present, otherwise a Supabase
 * access token. Returns a plain object rather than HeadersInit so callers
 * can spread/merge ergonomically.
 */
export async function buildAuthHeaders(
  extra?: Record<string, string>,
): Promise<Record<string, string>> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(extra ?? {}),
  };

  const apiKey = getApiKey();
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  } else {
    const token = await getSupabaseAccessToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
  }

  const teamId = readStorage(TEAM_STORAGE_KEY);
  if (teamId) {
    headers['X-Team-Id'] = teamId;
  }

  return headers;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly requestId?: string;
  readonly details?: unknown;

  constructor(
    message: string,
    status: number,
    opts: { code?: string; requestId?: string; details?: unknown } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = opts.code;
    this.requestId = opts.requestId;
    this.details = opts.details;
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, 'body' | 'headers'> {
  /** JSON body; will be stringified. Use `raw` for non-JSON payloads. */
  json?: unknown;
  /** Raw body; overrides `json`. Caller owns Content-Type in `headers`. */
  raw?: BodyInit | null;
  /** Extra headers merged on top of auth headers. */
  headers?: Record<string, string>;
  /** Query params appended to the URL. Skips undefined / null. */
  query?: Record<string, string | number | boolean | undefined | null>;
  /** Absolute URL override — skip getApiUrl base resolution. */
  absolute?: boolean;
}

function buildUrl(path: string, opts: ApiRequestOptions): string {
  const base = opts.absolute ? path : `${getApiUrl()}${path}`;
  if (!opts.query) return base;

  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query)) {
    if (v === undefined || v === null) continue;
    usp.set(k, String(v));
  }
  const qs = usp.toString();
  if (!qs) return base;
  return `${base}${base.includes('?') ? '&' : '?'}${qs}`;
}

/**
 * Low-level request: returns the raw Response. Throws ApiError on non-2xx.
 * Use this when you need Blob/stream bodies; prefer `apiJson` for JSON.
 */
export async function apiFetch(
  path: string,
  options: ApiRequestOptions = {},
): Promise<Response> {
  const { json, raw, headers: extraHeaders, query: _query, absolute: _absolute, ...rest } = options;
  const url = buildUrl(path, options);
  const headers = await buildAuthHeaders(extraHeaders);

  let body: BodyInit | null | undefined = raw ?? undefined;
  if (body === undefined && json !== undefined) {
    body = JSON.stringify(json);
  }
  // Let raw uploads (FormData, Blob) set their own Content-Type
  if (raw && !extraHeaders?.['Content-Type']) {
    delete headers['Content-Type'];
  }

  const response = await fetch(url, { ...rest, headers, body });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response;
}

async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('x-request-id') ?? undefined;
  try {
    const text = await response.text();
    if (!text) {
      return new ApiError(
        `HTTP ${response.status}`,
        response.status,
        { requestId },
      );
    }
    try {
      const body = JSON.parse(text);
      const message = body?.error ?? body?.detail ?? `HTTP ${response.status}`;
      return new ApiError(message, response.status, {
        code: body?.code,
        requestId: body?.request_id ?? requestId,
        details: body?.details,
      });
    } catch {
      return new ApiError(text.slice(0, 200), response.status, { requestId });
    }
  } catch {
    return new ApiError(`HTTP ${response.status}`, response.status, { requestId });
  }
}

/**
 * JSON-in, JSON-out. Returns the parsed body typed as `T`.
 * Most services should use this.
 */
export async function apiJson<T = unknown>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const response = await apiFetch(path, options);
  if (response.status === 204) {
    return undefined as unknown as T;
  }
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T = unknown>(path: string, options: ApiRequestOptions = {}) =>
    apiJson<T>(path, { ...options, method: 'GET' }),
  post: <T = unknown>(path: string, body?: unknown, options: ApiRequestOptions = {}) =>
    apiJson<T>(path, { ...options, method: 'POST', json: body }),
  patch: <T = unknown>(path: string, body?: unknown, options: ApiRequestOptions = {}) =>
    apiJson<T>(path, { ...options, method: 'PATCH', json: body }),
  put: <T = unknown>(path: string, body?: unknown, options: ApiRequestOptions = {}) =>
    apiJson<T>(path, { ...options, method: 'PUT', json: body }),
  delete: <T = unknown>(path: string, options: ApiRequestOptions = {}) =>
    apiJson<T>(path, { ...options, method: 'DELETE' }),
};
