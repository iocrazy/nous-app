// frontend/services/generatedService.ts
// Client for the Generated inbox — `/api/v1/generated`
// (backend/app/api/generated_router.py, spec §6.1), plus the one sibling route
// that lives under `/api/v1/resources` because its subject is a resource:
// `saveResourceAsAsset`. Same request body, same envelope, same error copy —
// see the note on that function for why it is not in `resourceService`.
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
  /**
   * 3a run provenance. Filled only for `agent_run` rows that are actually in
   * `run_deliverables`; null for everything else, historical rows included —
   * a card with no provenance is normal history, not a fault.
   *
   * Ids are STRINGS: they are Snowflake BIGINTs, and a JS number above 2^53
   * loses the low bits.
   *
   * ⚠️ `issue_id` is NOT a link. The issue route is keyed by the issue KEY and
   * needs a team, so the only URL that goes anywhere is the `deep_link` the
   * backend built; these three fields say WHICH run, for the reader and the
   * log.
   */
  issue_id: string | null;
  run_id: string | null;
  step: number | null;
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

/**
 * What `POST /resources/{id}/save-as-asset` answers: the generation shape plus
 * the inbox row that now registers the resource. The extra key is the whole
 * difference between the two endpoints' 201s (a backend test pins the key-set
 * difference at exactly `generated_id`), and it is a STRING like every other
 * Snowflake on the wire.
 */
export interface SaveResourceAsAssetResult extends SaveAsAssetResult {
  generated_id: string;
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
  /** Only this canvas's generations. Resolved server-side against
   *  `generated_media.canvas_id` — NOT client-side: under keyset pagination a
   *  client filter silently under-fills every page. */
  canvasId?: string;
  mediaKind?: string;
  model?: string;
  /** ISO 8601 instant; the router parses it as a datetime. */
  since?: string;
  /**
   * The asset a run was launched FROM (`generate_slot` stamps it). This is
   * what makes "this asset's generation history" a real question rather than
   * the scope's whole inbox rendered under one asset's name.
   */
  sourceAssetId?: string;
  /**
   * Show the machine-made `canvas_upload` rows the inbox hides by default —
   * masks, brush composites, and library assets transcoded purely so the
   * image-to-image bridge could fetch them. They are INPUTS to a generation,
   * not products anyone is being asked to keep or discard.
   *
   * Opt-in, matching the router: a caller that omits it gets the inbox the
   * user expects rather than a page padded with rows they cannot act on.
   */
  includeIntermediate?: boolean;
  cursor?: string;
  limit?: number;
}

// ─── Helpers ────────────────────────────────────────────────────────────────

const BASE = () => `${getApiUrl()}/api/v1/generated`;

/** The resources router, for the one call this module makes against it —
 *  `saveResourceAsAsset`. Same `?scope_id=` gate, same envelope. */
const RESOURCES_BASE = () => `${getApiUrl()}/api/v1/resources`;

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
    canvas_id: opts.canvasId,
    media_kind: opts.mediaKind,
    model: opts.model,
    since: opts.since,
    source_asset_id: opts.sourceAssetId,
    // Only sent when true. `include_intermediate=false` and the param being
    // absent are the same request, and spelling out a default is one more
    // place it can drift from the router's.
    include_intermediate: opts.includeIntermediate ? 'true' : undefined,
    cursor: opts.cursor,
    limit: opts.limit === undefined ? undefined : String(opts.limit),
  });
  for (const kind of opts.originKinds ?? []) qs.append('origin_kind', kind);

  const data = await envelopeFetch<unknown>(`${BASE()}?${qs}`, {
    headers: await getAuthHeaders(),
  });
  return normalizePage(data);
}

/**
 * ONE inbox card by id — the same wire shape {@link fetchGenerated} lists.
 *
 * The canvas's "As Asset" is the caller: an output node holds a
 * `GeneratedImageRef` whose `id` IS the `generated_media` row, and
 * `SaveAsAssetDialog` takes {@link GeneratedItem} rows, not canvas node data.
 * Reading the row rather than synthesizing one is what makes the dialog's
 * `source_asset_id` prefill real — that column is populated server-side and
 * the node never saw it.
 *
 * A row in another scope is a typed 404 ({@link GeneratedApiError}), never an
 * empty body: "not yours" and "not there" are the same answer to a caller who
 * may not learn which.
 */
export async function fetchGeneratedItem(
  scopeId: string,
  id: string,
): Promise<GeneratedItem> {
  return envelopeFetch<GeneratedItem>(`${BASE()}/${id}?${query(scopeId)}`, {
    headers: await getAuthHeaders(),
  });
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

/**
 * Every refusal code {@link saveResourceAsAsset} can raise that is NOT already
 * rendered by the generation path, mirrored from
 * `backend/app/api/resources_assets_router.py`.
 *
 * A TRIPWIRE, not a mechanism: nothing reads this list at runtime — the dialog
 * resolves `saveAsAsset.err.<code>` straight from whatever the server sent, so
 * an unlisted code still reaches the user (as the generic sentence). What the
 * list buys is that `saveAsAssetI18n.test.ts` fails when a code is added here
 * without copy in both locales, which forces whoever widens the backend's
 * error set to write the sentence a user will actually read.
 */
export const RESOURCE_SAVE_AS_ASSET_ERROR_CODES = [
  'resource_not_accessible',
  'resource_not_found',
  'resource_kind_unsupported',
  'resource_file_unresolved',
] as const;

/**
 * The same action for a LIBRARY RESOURCE — `POST /resources/{id}/save-as-asset`
 * (`backend/app/api/resources_assets_router.py`).
 *
 * It lives beside {@link saveGenerationAsAsset} rather than in
 * `resourceService`/`assetsService` because it is the same call with a
 * different subject: the request body is literally the backend's
 * `SaveAsAssetRequest` (one Pydantic class, pinned by a backend test), the
 * response is this module's {@link SaveAsAssetResult} plus one key, and every
 * refusal it can raise is read by the same `saveAsAsset.err.*` copy. Splitting
 * it across modules would mean two homes for one contract.
 *
 * Why the endpoint exists at all: only chat uploads and promoted generations
 * ever get a `generated_media` row, so a plain My Uploads file has nothing for
 * `/generated/{id}/save-as-asset` to take. The server finds-or-mints that row
 * inside the attach transaction — a cancelled dialog therefore leaves no
 * orphan inbox card, which is exactly why the client must NOT mint one first.
 *
 * Typed refusals a caller must be ready to render (all `GeneratedApiError`):
 * `not_a_member` (403, not in the target team), `resource_not_accessible`
 * (404, not yours / not there — one answer on purpose), `resource_not_found`
 * (404 from the attach: yours, but not filed in THIS team's library),
 * `resource_kind_unsupported` (422, no slot takes this file shape),
 * `resource_file_unresolved` (422, never downloaded / an album directory),
 * plus the asset codes the generation path already renders (`asset_not_found`,
 * `not_authorised`, `invalid_slot`, `loadout_mismatch`, `asset_exists`).
 *
 * `signal` is honoured and its `AbortError` is rethrown untouched: a cancel is
 * not evidence about the server (see `envelopeFetch`).
 */
export async function saveResourceAsAsset(
  scopeId: string,
  resourceId: string,
  body: SaveAsAssetBody,
  opts: { signal?: AbortSignal } = {},
): Promise<SaveResourceAsAssetResult> {
  const auth = await getAuthHeaders();
  return envelopeFetch<SaveResourceAsAssetResult>(
    `${RESOURCES_BASE()}/${resourceId}/save-as-asset?${query(scopeId)}`,
    {
      method: 'POST',
      headers: jsonHeaders(auth),
      body: JSON.stringify(body),
      signal: opts.signal,
    },
  );
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
