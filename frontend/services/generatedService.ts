// frontend/services/generatedService.ts
// Client for the Generated inbox — `/api/v1/generated`
// (backend/app/api/generated_router.py, spec §6.1).
//
// Scope is a `teams.id` snowflake and rides as `?scope_id=` on EVERY call,
// including the mutations: the router gates on team membership per request and
// answers 403 `not_a_member` without it.
//
// Ids are strings on the wire. The backend repository `_normalize`s BIGINT
// columns to strings precisely so JS never has to hold a Snowflake in a
// `number` (>2^53 loses precision) — mirror that here, never `number`.

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';
import { envelopeFetch, GeneratedApiError, jsonHeaders } from './apiEnvelope';
import type { AssetType } from '../components/assets/assetSlots';

export { GeneratedApiError };

// ─── Types (mirror app/schemas/generated.py, with string ids) ────────────────

/**
 * `deleted` is a real state a row can be read back in (a cleanup sample), but
 * it is NOT one of the filter tabs — see {@link GeneratedFilterState}.
 */
export type ReviewState = 'unreviewed' | 'saved' | 'in_assets' | 'deleted';

/** What `?state=` accepts. `all` is the router's alias for "no filter". */
export type GeneratedFilterState = 'unreviewed' | 'saved' | 'in_assets' | 'all';

export type BatchAction = 'save' | 'save_as_asset' | 'delete';

/**
 * Where a generation came from, already described for display.
 * `deep_link` is null for kinds with no route today (shot, chat) — the backend
 * returns null rather than inventing a URL, so the UI must branch on it.
 */
export interface GeneratedSource {
  kind: string;
  label: string;
  canvas_id: string | null;
  node_id: string | null;
  shot_id: string | null;
  conversation_id: string | null;
  deep_link: string | null;
}

export interface GeneratedItem {
  id: string;
  scope_id: string;
  media_kind: string;
  mime: string | null;
  prompt: string | null;
  model: string | null;
  provider: string | null;
  origin_kind: string;
  canvas_id: string | null;
  node_id: string | null;
  /** ISO 8601 — the router renders with `model_dump(mode="json")`. */
  created_at: string;
  promoted_resource_id: string | null;
  review_state: ReviewState;
  source_asset_id: string | null;
  // derived server-side
  source: GeneratedSource;
  title: string;
}

export interface GeneratedPage {
  items: GeneratedItem[];
  next_cursor: string | null;
}

/** Tab counters. There is no `deleted` counter because there is no such tab. */
export interface GeneratedCounts {
  unreviewed: number;
  saved: number;
  in_assets: number;
}

export interface NewAssetSpec {
  asset_type: AssetType;
  name: string;
}

/** Exactly one of `asset_id` / `new_asset` — the backend rejects both/neither. */
export interface SaveAsAssetBody {
  asset_id?: string;
  new_asset?: NewAssetSpec;
  slot?: string;
  loadout_id?: string;
}

export interface SaveAsAssetResult {
  generation: GeneratedItem;
  asset_id: string;
  resource_id: string;
}

export interface BatchFailure {
  id: string;
  code: string;
  detail: string;
}

/**
 * Per-id outcome. Partial failure survives the boundary — "12 of 20 saved" is
 * neither a success nor a 500, and the UI has to be able to say which 8 failed.
 */
export interface BatchResult {
  ok: string[];
  failed: BatchFailure[];
}

export interface BatchBody {
  ids: string[];
  action: BatchAction;
  save_as_asset?: SaveAsAssetBody;
}

export interface CleanupBody {
  older_than_days?: number;
  dry_run?: boolean;
}

export interface CleanupResult {
  dry_run: boolean;
  count: number;
  sample: GeneratedItem[];
  deleted: number;
  /**
   * One pass scans a bounded window: `count` is "matched in this pass", not
   * "matched ever". A UI that ignores this reports the cleanup as finished
   * while rows remain.
   */
  truncated: boolean;
}

export interface GeneratedListOptions {
  state?: GeneratedFilterState;
  originKinds?: string[];
  projectId?: string;
  mediaKind?: string;
  model?: string;
  /** ISO 8601 instant; the router parses it as a datetime. */
  since?: string;
  cursor?: string;
  limit?: number;
}

// ─── Helpers ────────────────────────────────────────────────────────────────

const BASE = () => `${getApiUrl()}/api/v1/generated`;

/** `?scope_id=` plus any extra params, in one place so no call can forget it. */
function query(scopeId: string, params: Record<string, string | undefined> = {}): URLSearchParams {
  const qs = new URLSearchParams({ scope_id: scopeId });
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) qs.set(key, value);
  }
  return qs;
}

/** POST, with a JSON body only when there is one. `saveGeneration` takes no
 *  body at all, and announcing a Content-Type for a payload that is not there
 *  is a claim about the request that is not true. */
async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const auth = await getAuthHeaders();
  if (body === undefined) return envelopeFetch<T>(url, { method: 'POST', headers: auth });
  return envelopeFetch<T>(url, {
    method: 'POST',
    headers: jsonHeaders(auth),
    body: JSON.stringify(body),
  });
}

/**
 * Coerce an untrusted page payload. `items` must never reach a caller as
 * `undefined` (regression #1457: a strip crashed into its error boundary on
 * exactly that shape).
 */
function normalizePage(raw: unknown): GeneratedPage {
  const page = (raw && typeof raw === 'object' ? raw : {}) as Partial<GeneratedPage>;
  return {
    items: Array.isArray(page.items) ? page.items : [],
    next_cursor: page.next_cursor ?? null,
  };
}

// ─── API calls ──────────────────────────────────────────────────────────────

/**
 * One page of the inbox. Omitted filters are omitted from the query string —
 * the router's own defaults (`state=unreviewed`, `limit=60`) then apply, so
 * "not supplied" and "supplied as the default" stay the same request.
 *
 * `originKinds` becomes a REPEATED `origin_kind=` param (FastAPI's
 * `List[str] = Query([])`); joining them with a comma would be read as one
 * kind literally named "a,b" and silently match nothing.
 */
export async function fetchGenerated(
  scopeId: string,
  opts: GeneratedListOptions = {},
): Promise<GeneratedPage> {
  const qs = query(scopeId, {
    state: opts.state,
    project_id: opts.projectId,
    media_kind: opts.mediaKind,
    model: opts.model,
    since: opts.since,
    cursor: opts.cursor,
    limit: opts.limit === undefined ? undefined : String(opts.limit),
  });
  for (const kind of opts.originKinds ?? []) qs.append('origin_kind', kind);

  const data = await envelopeFetch<unknown>(`${BASE()}?${qs}`, {
    headers: await getAuthHeaders(),
  });
  return normalizePage(data);
}

/** Tab counters for the current scope. */
export async function fetchGeneratedCounts(scopeId: string): Promise<GeneratedCounts> {
  return envelopeFetch<GeneratedCounts>(`${BASE()}/counts?${query(scopeId)}`, {
    headers: await getAuthHeaders(),
  });
}

/** Keep a generation in the library (`review_state` → `saved`). */
export async function saveGeneration(scopeId: string, id: string): Promise<GeneratedItem> {
  return postJson<GeneratedItem>(`${BASE()}/${id}/save?${query(scopeId)}`);
}

/** Keep a generation AND attach it to an asset slot (`review_state` → `in_assets`). */
export async function saveGenerationAsAsset(
  scopeId: string,
  id: string,
  body: SaveAsAssetBody,
): Promise<SaveAsAssetResult> {
  return postJson<SaveAsAssetResult>(`${BASE()}/${id}/save-as-asset?${query(scopeId)}`, body);
}

/** Discard a generation. Resolves only on a real 2xx; otherwise it throws. */
export async function deleteGeneration(scopeId: string, id: string): Promise<void> {
  await envelopeFetch<{ deleted: boolean }>(`${BASE()}/${id}?${query(scopeId)}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
}

/**
 * Apply one action to many ids. Resolves with per-id outcomes even when some
 * failed — callers MUST read `failed`, not just the absence of a throw.
 */
export async function batchGenerated(scopeId: string, body: BatchBody): Promise<BatchResult> {
  return postJson<BatchResult>(`${BASE()}/batch?${query(scopeId)}`, body);
}

/**
 * Purge old unreviewed generations. `dry_run` defaults to true server-side:
 * a caller that forgets the flag gets a preview, not a purge.
 */
export async function cleanupGenerated(
  scopeId: string,
  body: CleanupBody,
): Promise<CleanupResult> {
  return postJson<CleanupResult>(`${BASE()}/cleanup?${query(scopeId)}`, body);
}
