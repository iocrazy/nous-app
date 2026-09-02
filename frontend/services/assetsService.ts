// frontend/services/assetsService.ts
// Client for `/api/v1/assets` (backend/app/api/assets_router.py).
//
// Two layers, in this order:
//
//   1. The "Save as asset" DIALOG's slice — `searchAssets` / `createAsset` /
//      `fetchAsset`, over the narrow `AssetSummary` / `AssetDetail` views.
//   2. The LIBRARY PAGES' surface — the rest of the router (list with the full
//      filter set, counts, PATCH/DELETE/duplicate, files, links, loadouts,
//      prompt AI, slot generation, project refs), over `AssetRow` /
//      `AssetRowDetail`, which mirror the wire row field for field.
//
// The two type families coexist on purpose; the note above `AssetRow` says why.
//
// Every call takes `scopeId` and sends it as `?scope_id=` — the router gates on
// team membership per request and answers 403 `not_a_member` without it.
//
// Ids are strings for the same reason as in generatedService: the repository
// stringifies BIGINT columns so a Snowflake never lands in a JS `number`.

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';
import { envelopeFetch, GeneratedApiError, jsonHeaders } from './apiEnvelope';
// The six types are NOT re-declared here: `assetSlots.ts` is already the
// frontend's single mirror of the backend table, and a second copy in this
// file would be a second thing to forget when a type is added.
import type { AssetType } from '../components/assets/assetSlots';

export { GeneratedApiError };
export type { AssetType };

/**
 * Readiness is DERIVED server-side (`app/services/assets/slots.py::readiness`),
 * never stored: `ready` iff the type's primary slot holds a file (for `prompt`,
 * iff its body is non-blank). `missing` names what is still needed.
 */
export interface AssetReadiness {
  state: 'ready' | 'draft';
  missing: string[];
}

/**
 * The subset of an asset row this module's callers read.
 *
 * `scope_id` is null ONLY for global system presets (`assets_scope_or_preset`
 * CHECK) — those are visible from every scope and are read-only, so the dialog
 * must be able to show them without being able to write to them.
 */
export interface AssetSummary {
  id: string;
  scope_id: string | null;
  asset_type: AssetType;
  name: string;
  role_tag: string;
  readiness: AssetReadiness;
  cover_file_id: string | null;
  is_system_preset: boolean;
}

/** A character's "outfit" subset. Only what the slot picker needs. */
export interface AssetLoadout {
  id: string;
  name: string;
  is_default: boolean;
}

export interface AssetDetail extends AssetSummary {
  loadouts: AssetLoadout[];
}

/**
 * Which side of `assets.in_library` a read wants (mig 449).
 *
 * `in` — the library: what somebody deliberately added. The server's own
 * default, and what every shelf and picker means by "my assets".
 * `out` — the project-originated rows nobody has adopted yet (script imports,
 * migrated legacy entities). What the shelf's chip shows when the user asks
 * "what is waiting to be added?".
 * `all` — both. The project workspace's asset pages, whose job is showing what
 * the PROJECT uses regardless of adoption.
 */
export type AssetLibraryFilter = 'in' | 'out' | 'all';

export interface AssetSearchOptions {
  type?: AssetType;
  q?: string;
  limit?: number;
  /**
   * Omitted means the SERVER default, `in` — which is what every caller of
   * this function wants: attaching a generation to an asset, or linking one
   * into a project, targets the user's library, not the pile of names a script
   * happened to mention. Passed through rather than hard-coded so a caller
   * that genuinely needs to widen can, without a second function.
   */
  library?: AssetLibraryFilter;
}

/**
 * How an asset came to exist (`assets_source_check`, mirrored from
 * `app/schemas/assets.py::AssetSource`). It is provenance, written once at
 * creation and never corrected afterwards, so a caller that omits it does not
 * get "unknown" — it gets the server default `manual`, which is a claim.
 */
export const ASSET_SOURCES = [
  'manual',
  'script_import',
  'generated',
  'migrated',
  'duplicated',
  'system_preset',
] as const;

export type AssetSource = (typeof ASSET_SOURCES)[number];

/**
 * The subset a client may CLAIM on create (`app/schemas/assets.py::
 * AssetCreateSource`). The API rejects the other four with a 422 — they are
 * assertions only the server can honestly make, each written alongside the row
 * that makes it true (`duplicated_from` for `duplicated`, the preset flag for
 * `system_preset`). Provenance is written once and never corrected, so a
 * forged one is permanent.
 *
 * Deliberately NOT `AssetSource`: a create body typed as the response
 * vocabulary compiles for values the server refuses, turning a type error at
 * the call site into a 422 at runtime.
 */
export type AssetCreateSource = Extract<AssetSource, 'manual' | 'generated'>;

export interface AssetCreateBody {
  asset_type: AssetType;
  name: string;
  /**
   * The two descriptive fields `AssetCreate` accepts alongside the name
   * (`role_tag` max 40, `description` max 20000, both defaulting to `""`
   * server-side).
   *
   * They are here rather than left to a follow-up PATCH so the shelf's "New
   * asset" form is ONE request: a create that succeeded followed by a PATCH
   * that failed would leave a half-filled asset behind with nothing to
   * distinguish it from one the user meant to leave blank.
   */
  role_tag?: string;
  description?: string;
  /**
   * Defaults to `manual` SERVER-side. Any caller creating an asset on behalf
   * of a generation must pass `generated` explicitly: the backend's own
   * create-and-attach path (`generated_inbox_service.save_as_asset`) does, and
   * a two-step client that skips it writes a permanently wrong provenance
   * that nothing downstream can distinguish from a hand-made asset.
   */
  source?: AssetCreateSource;
}

const BASE = () => `${getApiUrl()}/api/v1/assets`;

function query(scopeId: string, params: Record<string, string | undefined> = {}): URLSearchParams {
  const qs = new URLSearchParams({ scope_id: scopeId });
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) qs.set(key, value);
  }
  return qs;
}

/** Assets in this scope (plus the global presets the backend unions in). */
export async function searchAssets(
  scopeId: string,
  opts: AssetSearchOptions = {},
): Promise<AssetSummary[]> {
  const qs = query(scopeId, {
    type: opts.type,
    q: opts.q,
    library: opts.library,
    limit: opts.limit === undefined ? undefined : String(opts.limit),
  });
  const data = await envelopeFetch<unknown>(`${BASE()}?${qs}`, {
    headers: await getAuthHeaders(),
  });
  return Array.isArray(data) ? (data as AssetSummary[]) : [];
}

/**
 * Create an asset. A name+type collision inside the scope throws
 * {@link GeneratedApiError} with `code === 'asset_exists'` and
 * `extra.existing_asset_id` — the caller can offer "use the existing one"
 * instead of dead-ending the user.
 */
export async function createAsset(
  scopeId: string,
  body: AssetCreateBody,
): Promise<AssetSummary> {
  return envelopeFetch<AssetSummary>(`${BASE()}?${query(scopeId)}`, {
    method: 'POST',
    headers: jsonHeaders(await getAuthHeaders()),
    body: JSON.stringify(body),
  });
}

/**
 * One asset, with its loadouts. `loadouts` is normalized to `[]` when absent so
 * a caller mapping over it cannot crash on `undefined`.
 */
export async function fetchAsset(scopeId: string, id: string): Promise<AssetDetail> {
  const data = await envelopeFetch<Partial<AssetDetail>>(`${BASE()}/${id}?${query(scopeId)}`, {
    headers: await getAuthHeaders(),
  });
  return {
    ...(data as AssetDetail),
    loadouts: Array.isArray(data?.loadouts) ? data.loadouts : [],
  };
}

// ─── The full row (library pages) ───────────────────────────────────────────
//
// `AssetSummary` / `AssetDetail` above are the SAVE-AS-ASSET DIALOG's narrow
// view: the handful of fields it reads, and nothing else. The wire always
// carries the whole row, so the types below mirror `AssetResponse` /
// `AssetDetailResponse` field for field — an `AssetRow` is structurally
// assignable everywhere an `AssetSummary` is expected, which is why the two
// coexist instead of one being widened into the other. Widening the narrow one
// would force every existing fixture to grow twenty fields it never reads.

/** How an asset relates to another (`slots.py::LINK_RULES`). */
export type AssetLinkRelation = 'wears' | 'holds' | 'ambience_of' | 'voice_of';

/**
 * One row of `assets`, exactly as `_serialize(with_derived(...))` emits it.
 * Every BIGINT column is a JSON string; `readiness` / `file_counts_by_slot` /
 * `project_ids` / `loadout_count` are derived server-side and are not columns.
 */
export interface AssetRow {
  id: string;
  scope_id: string | null;
  asset_type: AssetType;
  subtype: string | null;
  name: string;
  role_tag: string;
  description: string;
  attrs: Record<string, unknown>;
  prompt_positive: string | null;
  prompt_negative: string | null;
  prompt_positive_zh: string | null;
  prompt_negative_zh: string | null;
  platform_params: Record<string, unknown>;
  cover_file_id: string | null;
  source: AssetSource;
  duplicated_from: string | null;
  is_system_preset: boolean;
  /**
   * Library membership (mig 449) — EXPLICIT, and distinct from `source`.
   *
   * `true` means somebody deliberately put this asset in the scope's library
   * (created it, duplicated one, saved a generation as one). `false` means it
   * arrived as a side effect of project work — a script import or the legacy
   * migration — and lives on its project's page until someone adds it.
   *
   * Do NOT re-derive this from `source`: membership is toggled by the user,
   * provenance is written once and never corrected, and an imported character
   * the user adopted has `source === 'script_import'` with `in_library` true.
   */
  in_library: boolean;
  /** jsonb OBJECT of group → values (`{"role": ["lead"]}`), not a flat array. */
  tags: Record<string, unknown>;
  sort_order: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  readiness: AssetReadiness;
  file_counts_by_slot: Record<string, number>;
  project_ids: string[];
  loadout_count: number;
}

/** One attached file. The resource is NOT moved or copied — this is a pointer. */
export interface AssetFileRow {
  asset_id: string;
  resource_id: string;
  slot: string;
  loadout_id: string | null;
  sort_order: number;
  note: string | null;
  attached_by: string | null;
  attached_at: string;
}

export interface AssetLinkRow {
  from_asset_id: string;
  to_asset_id: string;
  relation: AssetLinkRelation;
  created_at: string | null;
}

/** A loadout in full (the dialog's {@link AssetLoadout} is its first three fields). */
export interface AssetLoadoutRow {
  id: string;
  asset_id: string;
  name: string;
  is_default: boolean;
  costume_ids: string[];
  prop_ids: string[];
  prompt_extra: string | null;
  sort_order: number;
  created_at: string;
}

/**
 * One canvas that references this asset (`used_in.canvases`, P4 Task 1).
 *
 * `node_ids` is a LIST because a canvas may place the same asset on several
 * cards; the server aggregates per canvas so a board using it three times
 * appears ONCE. Every id is a string, the boundary's rule for Snowflakes.
 */
export interface UsedInCanvasRef {
  canvas_id: string;
  canvas_name: string;
  /** `smart` / `storyboard` / `character` / … — the canvas row's own kind. */
  kind: string;
  project_id: string;
  node_ids: string[];
  loadout_ids: string[];
}

/**
 * Where this asset is in use (spec §5.1).
 *
 * `storyboards` is declared and ALWAYS EMPTY today: the storyboard side has no
 * ref mirror yet. It is on the wire rather than absent so a client renders "no
 * storyboard usage" instead of branching on a missing key — and so the day it
 * starts filling, nothing about the shape has to change.
 */
export interface AssetUsedIn {
  canvases: UsedInCanvasRef[];
  storyboards: unknown[];
}

export interface AssetRowDetail extends AssetRow {
  files: AssetFileRow[];
  /** Outgoing links (this asset → another). */
  links: AssetLinkRow[];
  /** Incoming links (another asset → this one). A different question. */
  linked_by: AssetLinkRow[];
  loadouts: AssetLoadoutRow[];
  /**
   * OPTIONAL, like the wire — `undefined` means NOBODY ASKED.
   *
   * `used_in` is opt-in (`fetchAssetDetail(..., { usedIn: true })`) because it
   * costs a five-table aggregate server-side and only the asset sheet's Used
   * In panel renders it, while a canvas can hold dozens of asset cards each
   * fetching their own detail on mount.
   *
   * Three states, and they are three different facts: `undefined` (not
   * asked), present with empty lists (asked, used nowhere), present and
   * populated. Defaulting the first into the second would make a panel say
   * "Used nowhere" about an answer nobody computed.
   */
  used_in?: AssetUsedIn;
}

/** Per-type tallies for the sidebar badges (`GET /assets/counts`). */
export type AssetCounts = Record<AssetType, number>;

// ─── Request bodies ─────────────────────────────────────────────────────────

/**
 * PATCH body. OMIT a key to leave the field alone; send an explicit `null` to
 * CLEAR it. The two are different requests and the backend reads them as such
 * (`AssetUpdate`, `exclude_unset`), so building this object with
 * `{...form}` — which materializes every key — silently turns "unchanged" into
 * "cleared". Send only what the user actually edited.
 *
 * Unknown keys are rejected (`extra="forbid"`): a typo'd field is a 422, not a
 * 200 that quietly did nothing.
 *
 * `in_library` is absent because the SERVER does not accept it here either:
 * `AssetUpdate` does not declare the field and forbids extras, so a PATCH
 * aimed at it is a 422. Library membership has one write path,
 * {@link setAssetLibraryMembership}.
 */
export interface AssetUpdateBody {
  name?: string;
  subtype?: string | null;
  role_tag?: string;
  description?: string;
  attrs?: Record<string, unknown>;
  prompt_positive?: string | null;
  prompt_negative?: string | null;
  prompt_positive_zh?: string | null;
  prompt_negative_zh?: string | null;
  platform_params?: Record<string, unknown>;
  cover_file_id?: string | null;
  tags?: Record<string, unknown>;
  sort_order?: number;
}

export interface AttachFileBody {
  resource_id: string;
  /** Defaults to `unsorted` server-side; `slotsFor(type)` lists the valid ones. */
  slot?: string;
  loadout_id?: string | null;
  note?: string | null;
}

export interface LoadoutCreateBody {
  name: string;
  costume_ids?: string[];
  prop_ids?: string[];
  prompt_extra?: string | null;
}

/** PATCH semantics again: omitted keys stay as they are. */
export interface LoadoutUpdateBody {
  name?: string;
  costume_ids?: string[];
  prop_ids?: string[];
  prompt_extra?: string | null;
  is_default?: boolean;
  sort_order?: number;
}

export interface AssetListOptions {
  type?: AssetType;
  projectId?: string;
  q?: string;
  readiness?: 'ready' | 'draft';
  /** One tag VALUE; matched across every tag group server-side. */
  tag?: string;
  /** Omitted means the server default, `in` — the library shelf. */
  library?: AssetLibraryFilter;
  sort?: 'recent' | 'name' | 'readiness';
  limit?: number;
  offset?: number;
}

/**
 * The dry run of {@link generateSlot} — what it would compose, with no
 * provider call and no writes.
 *
 * Read the fields for what they ARE: `positive` and `aspect_ratio` go to the
 * provider, `negative` does NOT (no image adapter here accepts one — it is
 * recorded on the generation as provenance), and `reference_resource_ids` is
 * INTENT. What actually went is in the run's `skipped_references`.
 */
export interface GenerateSlotPreview {
  positive: string;
  negative: string;
  reference_resource_ids: string[];
  aspect_ratio: string;
  model: string | null;
}

/** One unit of a run that failed. `index` is its position in the requested count. */
export interface GenerateSlotFailure {
  index: number;
  code: string;
  detail: string;
}

/** A reference the run could not materialize, and why. */
export interface SkippedReference {
  resource_id: string;
  reason: string;
}

/**
 * The 202 from {@link generateSlot}.
 *
 * The id list is `generation_ids` — NOT `generations`. The products do not
 * join the asset: they land in the Generated inbox as `unreviewed`, and the
 * user picks which one gets attached.
 *
 * `failed` and `skipped_references` are the honest half and must be rendered.
 * A run can succeed for three of four units, or succeed having silently
 * dropped the asset's own reference image; reading only `generation_ids`
 * reports both as unqualified success.
 */
export interface GenerateSlotResult {
  generation_ids: string[];
  failed: GenerateSlotFailure[];
  skipped_references: SkippedReference[];
  inbox_state: 'unreviewed';
}

export interface GenerateSlotBody {
  slot: string;
  loadout_id?: string | null;
  model?: string | null;
  /** 1–4 server-side; a larger number is a 422, not a silently clamped run. */
  count?: number;
}

// ─── Bundle delivery protocol ───────────────────────────────────────────────

/**
 * Why a reference the asset owns is not in the delivered list. A CLOSED set,
 * mirroring `DroppedReason` in `backend/app/schemas/assets.py` — the backend's
 * response model validates it, so a value outside these three never reaches
 * the wire.
 *
 * `provider_no_refs` is not a flavour of `over_limit`: the provider takes no
 * references at all, and the remedy is a different model rather than fewer
 * picks. A UI that collapses the two sends people unpicking references that
 * were never going to be sent.
 */
export type DroppedReason = 'no_image_file' | 'over_limit' | 'provider_no_refs';

export interface DroppedReference {
  resource_id: string;
  reason: DroppedReason;
}

/**
 * What an asset hands a generator running one specific model (spec §6.3).
 *
 * `dropped` is the load-bearing half and must be rendered: every reference
 * IN SCOPE OF THE REQUEST that is not in `reference_resource_ids` is in there
 * with a reason. `selectedFileIds` is what bounds that scope — with one, both
 * lists describe the caller's own picks, so a run in which everything chosen
 * was sent reports nothing rather than a standing alarm about files the caller
 * never asked for. Reading only the id list reports a trimmed delivery as a complete
 * one — the recorded "选了也生成了但图里没有" failure.
 *
 * `max_refs` is the PROVIDER's ceiling, echoed so a caller can say "2 of 5
 * sent" without inferring it from the two list lengths (which would read 2 as
 * the ceiling for an asset that only owns two files).
 */
export interface AssetBundle {
  prompt: { positive: string; negative: string };
  reference_resource_ids: string[];
  dropped: DroppedReference[];
  max_refs: number;
}

export interface BundleOptions {
  /** A catalog row NAME, the same vocabulary the model picker shows. */
  model: string;
  loadoutId?: string;
  /**
   * The caller's checklist — the resource ids it wants considered.
   *
   * THREE states, and the third is the one that needs the care:
   *
   *  * `undefined` — no checklist. Every file the asset owns is a candidate,
   *    which is what the asset sheet asks.
   *  * a non-empty array — those files, and only those.
   *  * an EMPTY array — the user unticked everything. Sent as one empty value
   *    (`?selected_file_ids=`) because a zero-length repeated parameter is
   *    indistinguishable from an absent one on the wire, and the backend reads
   *    absent as "send them all". Collapsing the two here would ship exactly
   *    the references the user just removed.
   *
   * Passing it is what makes the provider's ceiling trim the right population.
   * Trimming server-side over everything and intersecting client-side after is
   * the SAME two steps in the wrong order, and it delivers nothing whenever
   * the picks are not a prefix of the priority order.
   */
  selectedFileIds?: readonly string[];
}

/**
 * Compose this asset's delivery payload for `model`.
 *
 * `model` is required because the answer depends on it — the reference ceiling
 * belongs to the provider, so the same asset bundles differently for codex
 * (nine references) and for ark (none). A read: no provider call, no writes,
 * no cost.
 */
export async function fetchBundle(
  scopeId: string,
  assetId: string,
  opts: BundleOptions,
): Promise<AssetBundle> {
  const qs = query(scopeId, { model: opts.model, loadout_id: opts.loadoutId });
  if (opts.selectedFileIds !== undefined) {
    // `append`, not `set`: the parameter is repeatable. The empty-array arm
    // still appends once — see `BundleOptions.selectedFileIds`.
    if (opts.selectedFileIds.length === 0) qs.append('selected_file_ids', '');
    else for (const id of opts.selectedFileIds) qs.append('selected_file_ids', id);
  }
  return envelopeFetch<AssetBundle>(`${BASE()}/${assetId}/bundle?${qs}`, {
    headers: await getAuthHeaders(),
  });
}

// ─── Helpers ────────────────────────────────────────────────────────────────

async function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  const auth = await getAuthHeaders();
  // No body → no Content-Type. Announcing a JSON payload that is not there is
  // a claim about the request that is not true (mirrors generatedService).
  if (body === undefined) return envelopeFetch<T>(url, { method, headers: auth });
  return envelopeFetch<T>(url, {
    method,
    headers: jsonHeaders(auth),
    body: JSON.stringify(body),
  });
}

/** `loadouts` / `files` / `links` normalized to arrays — a caller mapping over
 *  one must not crash on `undefined` from a partial payload. */
function normalizeDetail(raw: unknown): AssetRowDetail {
  const row = (raw && typeof raw === 'object' ? raw : {}) as Partial<AssetRowDetail>;
  return {
    ...(row as AssetRowDetail),
    files: Array.isArray(row.files) ? row.files : [],
    links: Array.isArray(row.links) ? row.links : [],
    linked_by: Array.isArray(row.linked_by) ? row.linked_by : [],
    loadouts: Array.isArray(row.loadouts) ? row.loadouts : [],
    // NOT defaulted, unlike the four arrays above — that is the whole point of
    // the opt-in. A response with no `used_in` (nobody asked, or an older
    // backend) stays `undefined`; only a response that HAS one is normalized,
    // and then each list is checked on its own so a payload carrying
    // `canvases` but no `storyboards` does not lose the half it did send.
    used_in:
      row.used_in === undefined || row.used_in === null
        ? undefined
        : {
            canvases: Array.isArray(row.used_in.canvases) ? row.used_in.canvases : [],
            storyboards: Array.isArray(row.used_in.storyboards)
              ? row.used_in.storyboards
              : [],
          },
  };
}

// ─── Assets ─────────────────────────────────────────────────────────────────

/**
 * The shelf query — the full filter set, unlike {@link searchAssets} (which is
 * the dialog's three-knob slice of the same endpoint).
 *
 * Omitted filters are omitted from the query string, so the router's own
 * defaults apply and "not supplied" stays the same request as "supplied as the
 * default". Every value here is enumerated server-side: an unknown `sort` or
 * `readiness` is a 422 rather than an unfiltered shelf that looks filtered.
 */
export async function listAssets(
  scopeId: string,
  opts: AssetListOptions = {},
): Promise<AssetRow[]> {
  const qs = query(scopeId, {
    type: opts.type,
    project_id: opts.projectId,
    q: opts.q,
    readiness: opts.readiness,
    tag: opts.tag,
    library: opts.library,
    sort: opts.sort,
    limit: opts.limit === undefined ? undefined : String(opts.limit),
    offset: opts.offset === undefined ? undefined : String(opts.offset),
  });
  const data = await envelopeFetch<unknown>(`${BASE()}?${qs}`, {
    headers: await getAuthHeaders(),
  });
  return Array.isArray(data) ? (data as AssetRow[]) : [];
}

/**
 * Every asset linked to one project, across the project's own asset scope.
 *
 * The project workspace's asset panels (P3) are the caller. Note what this
 * route does NOT take: a `scope_id`. The scope is derived from the project —
 * and from its OWNER, not the caller — so a collaborator on someone else's
 * personal project reads the same shelf the owner does instead of an empty
 * one aimed at their own personal team.
 */
export async function listProjectAssets(
  projectId: string,
  type?: AssetType,
): Promise<AssetRow[]> {
  // No scope_id: this route derives the scope from the project itself (a
  // personal project resolves to its OWNER's personal team, not the caller's).
  const qs = new URLSearchParams();
  if (type) qs.set('type', type);
  const suffix = qs.toString() ? `?${qs}` : '';
  const data = await envelopeFetch<unknown>(
    `${getApiUrl()}/api/v1/projects/${projectId}/assets${suffix}`,
    { headers: await getAuthHeaders() },
  );
  return Array.isArray(data) ? (data as AssetRow[]) : [];
}

/** Per-type tallies for the sidebar badges. Zero-filled server-side, so every
 *  type is present — a 0 means "none yet", never "unknown". */
export async function fetchAssetCounts(scopeId: string): Promise<AssetCounts> {
  return envelopeFetch<AssetCounts>(`${BASE()}/counts?${query(scopeId)}`, {
    headers: await getAuthHeaders(),
  });
}

export interface AssetDetailOptions {
  /**
   * Ask for `used_in` — the Used In panel's canvases.
   *
   * OFF by default, and that is a cost decision with a visible consequence:
   * server-side it is a five-table aggregate, and a canvas board can hold
   * dozens of asset cards that each fetch their own detail on mount. A caller
   * that does not set this gets `used_in === undefined`, which means "not
   * asked" — never "used nowhere".
   *
   * The sheet sets it. The canvas card, the asset picker, the legacy-card
   * migration and the seeding path all render the asset's face only, so they
   * do not.
   */
  usedIn?: boolean;
}

/** One asset with its files, links and loadouts (and, on request, its usage). */
export async function fetchAssetDetail(
  scopeId: string,
  id: string,
  opts: AssetDetailOptions = {},
): Promise<AssetRowDetail> {
  const qs = query(scopeId, {
    include_used_in: opts.usedIn ? 'true' : undefined,
  });
  return normalizeDetail(
    await envelopeFetch<unknown>(`${BASE()}/${id}?${qs}`, {
      headers: await getAuthHeaders(),
    }),
  );
}

/**
 * The three pre-P3 canvas card kinds `GET /assets/resolve-legacy` can map.
 *
 * These are the smart node TYPES the old entity canvases wrote
 * (`character` / `location` / `prop`), and they are also the two legacy tables'
 * vocabulary — `character` came from `_legacy_project_characters`, the other
 * two from `_legacy_project_lib_entities`. The backend owns that mapping; the
 * client only has to name the kind.
 */
export type LegacyEntityKind = 'character' | 'location' | 'prop';

/**
 * The asset a pre-P3 canvas card became, or `null` when there is none.
 *
 * `null` is a 200, not an error: the question was well formed and the answer is
 * "no asset in this scope carries that provenance" — the entity's project
 * never migrated. (Adoption used to be a second cause; the migration now
 * stamps the adopted asset's `attrs.legacy_ids`, so an entity merged into a
 * hand-made asset resolves like any other. Rows migrated before that change
 * still answer `null` until the backfill is re-run.) A caller must treat
 * `null` as "unmigrated" and leave the legacy card alone. Matching on name or
 * type instead would rewire a card to an asset nobody chose.
 *
 * `legacyId` is a Snowflake string on the way out; the router parses it back to
 * the JSON number the migration wrote.
 */
export async function resolveLegacyAsset(
  scopeId: string,
  kind: LegacyEntityKind,
  legacyId: string,
): Promise<string | null> {
  const data = await envelopeFetch<{ asset_id?: string | null } | null>(
    `${BASE()}/resolve-legacy?${query(scopeId, { kind, legacy_id: legacyId })}`,
    { headers: await getAuthHeaders() },
  );
  return data?.asset_id ?? null;
}

/**
 * Edit an asset. Pass ONLY the fields the user changed — see
 * {@link AssetUpdateBody} for why an over-complete body clears things.
 *
 * A system preset answers 403 `system_preset_readonly`: duplicate it first.
 */
export async function updateAsset(
  scopeId: string,
  id: string,
  body: AssetUpdateBody,
): Promise<AssetRow> {
  return sendJson<AssetRow>(`${BASE()}/${id}?${query(scopeId)}`, 'PATCH', body);
}

/** Soft-delete. Resolves only on a real 2xx; otherwise it throws. */
export async function deleteAsset(scopeId: string, id: string): Promise<void> {
  await sendJson<{ deleted: boolean }>(`${BASE()}/${id}?${query(scopeId)}`, 'DELETE');
}

/**
 * Add this asset to the scope's library, or take it out (mig 449).
 *
 * The ONLY way to change membership. `PATCH {"in_library": ...}` is a 422 — the
 * server does not declare the field — so this is not a preferred path among
 * two, it is the path. A PATCH body is assembled from a form under
 * `exclude_unset` anyway, so flipping a flag through one is how unrelated
 * fields get rewritten; these two routes send no body at all.
 *
 * ⚠️ Removal is NOT a delete and NOT an unlink. The asset keeps its files,
 * links, loadouts and every project reference, and its project pages keep
 * showing it — only the library shelf and the sidebar badges stop counting it.
 * {@link deleteAsset} is the destructive one.
 *
 * Idempotent: adding one that is already in answers 200 with the same row, so
 * a double-clicked button does not report a failure for work that is done.
 * Answers the UPDATED row, so the caller re-renders the card it acted on
 * instead of guessing the new state or re-fetching.
 *
 * A system preset answers 403 `system_preset_readonly` — a global row's
 * membership is not one team's to change.
 */
export async function setAssetLibraryMembership(
  scopeId: string,
  id: string,
  inLibrary: boolean,
): Promise<AssetRow> {
  return sendJson<AssetRow>(
    `${BASE()}/${id}/library?${query(scopeId)}`,
    inLibrary ? 'POST' : 'DELETE',
  );
}

/**
 * Copy an asset — files, links and loadouts included — into this scope.
 * Answers the DETAIL row, so the caller can open the copy without a second
 * round trip. Omitting `name` means "{source} (copy)".
 *
 * This is the only way to edit a system preset.
 */
export async function duplicateAsset(
  scopeId: string,
  id: string,
  name?: string,
): Promise<AssetRowDetail> {
  return normalizeDetail(
    await sendJson<unknown>(
      `${BASE()}/${id}/duplicate?${query(scopeId)}`,
      'POST',
      name === undefined ? {} : { name },
    ),
  );
}

// ─── Files ──────────────────────────────────────────────────────────────────

/** Attach one existing resource to a slot. The file is not moved or copied. */
export async function attachFile(
  scopeId: string,
  assetId: string,
  body: AttachFileBody,
): Promise<AssetFileRow> {
  return sendJson<AssetFileRow>(
    `${BASE()}/${assetId}/files?${query(scopeId)}`,
    'POST',
    body,
  );
}

/**
 * Attach several at once. All-or-nothing server-side: one bad item rolls the
 * whole batch back, so a rejection means nothing was attached — do not
 * re-render as if the good ones landed.
 */
export async function attachFiles(
  scopeId: string,
  assetId: string,
  items: AttachFileBody[],
): Promise<AssetFileRow[]> {
  const data = await sendJson<unknown>(
    `${BASE()}/${assetId}/files?${query(scopeId)}`,
    'POST',
    { items },
  );
  return Array.isArray(data) ? (data as AssetFileRow[]) : [];
}

/**
 * Detach one file from one slot. The resource itself is untouched.
 *
 * ⚠️ NO PRODUCTION CALLER, and unlike the P3 footholds in this file that is a
 * GAP, not a plan: `Equip` is the Board's headline action and P2 shipped no
 * inverse for it, so a file equipped onto the wrong slot can only be undone by
 * deleting the underlying resource or the whole asset. The endpoint, its scope
 * gate and its tests all exist — what is missing is the affordance (where the
 * control lives, its confirm, its refetch contract), which is a design call
 * and therefore P3. Recorded in the PR's 已知 so a user does not discover it.
 */
export async function detachFile(
  scopeId: string,
  assetId: string,
  resourceId: string,
  slot: string,
): Promise<void> {
  await sendJson<{ detached: boolean }>(
    // The slot is a path segment, so it must be encoded: an un-encoded value
    // containing a slash would silently address a different route.
    `${BASE()}/${assetId}/files/${resourceId}/${encodeURIComponent(slot)}?${query(scopeId)}`,
    'DELETE',
  );
}

// ─── Links ──────────────────────────────────────────────────────────────────

/** Relate two assets (`wears` / `holds` / `ambience_of` / `voice_of`). A pair
 *  the rules do not allow is a 422 `link_not_allowed`, not a no-op. */
export async function createLink(
  scopeId: string,
  assetId: string,
  toAssetId: string,
  relation: AssetLinkRelation,
): Promise<AssetLinkRow> {
  return sendJson<AssetLinkRow>(`${BASE()}/${assetId}/links?${query(scopeId)}`, 'POST', {
    to_asset_id: toAssetId,
    relation,
  });
}

export async function deleteLink(
  scopeId: string,
  assetId: string,
  toAssetId: string,
  relation: AssetLinkRelation,
): Promise<void> {
  await sendJson<{ removed: boolean }>(
    `${BASE()}/${assetId}/links/${toAssetId}/${relation}?${query(scopeId)}`,
    'DELETE',
  );
}

// ─── Loadouts ───────────────────────────────────────────────────────────────

export async function createLoadout(
  scopeId: string,
  assetId: string,
  body: LoadoutCreateBody,
): Promise<AssetLoadoutRow> {
  return sendJson<AssetLoadoutRow>(
    `${BASE()}/${assetId}/loadouts?${query(scopeId)}`,
    'POST',
    body,
  );
}

export async function updateLoadout(
  scopeId: string,
  assetId: string,
  loadoutId: string,
  body: LoadoutUpdateBody,
): Promise<AssetLoadoutRow> {
  return sendJson<AssetLoadoutRow>(
    `${BASE()}/${assetId}/loadouts/${loadoutId}?${query(scopeId)}`,
    'PATCH',
    body,
  );
}

/** Delete a loadout. The default one is refused (422) — promote another first. */
export async function deleteLoadout(
  scopeId: string,
  assetId: string,
  loadoutId: string,
): Promise<void> {
  await sendJson<{ deleted: boolean }>(
    `${BASE()}/${assetId}/loadouts/${loadoutId}?${query(scopeId)}`,
    'DELETE',
  );
}

// ─── Prompt AI ──────────────────────────────────────────────────────────────

/**
 * Translate this asset's prompt into `targetLang`. The source side is never
 * modified; a target that already holds text is skipped unless `force`.
 *
 * Nothing to translate is a 422 `nothing_to_translate` and an unreachable
 * agent is a 503 `translate_unavailable` — both typed refusals, so neither
 * arrives as a 200 over an unchanged asset. Render them.
 */
export async function translatePrompt(
  scopeId: string,
  assetId: string,
  targetLang: 'zh' | 'en',
  force = false,
): Promise<AssetRowDetail> {
  return normalizeDetail(
    await sendJson<unknown>(
      `${BASE()}/${assetId}/prompt/translate?${query(scopeId)}`,
      'POST',
      { target_lang: targetLang, force },
    ),
  );
}

/**
 * Reverse-engineer `prompt_positive` from the asset's primary-slot file.
 * Runs the vision agent IN-REQUEST (seconds to tens of seconds) and answers
 * with the written asset — there is no task id to poll.
 */
export async function regeneratePrompt(
  scopeId: string,
  assetId: string,
): Promise<AssetRowDetail> {
  return normalizeDetail(
    await sendJson<unknown>(
      `${BASE()}/${assetId}/prompt/regenerate?${query(scopeId)}`,
      'POST',
    ),
  );
}

// ─── Slot generation ────────────────────────────────────────────────────────

/** What a run would compose — no provider call, no writes, no cost. */
export async function previewGenerateSlot(
  scopeId: string,
  assetId: string,
  slot: string,
  loadoutId?: string,
): Promise<GenerateSlotPreview> {
  const qs = query(scopeId, { slot, loadout_id: loadoutId });
  return envelopeFetch<GenerateSlotPreview>(
    `${BASE()}/${assetId}/generate-slot/preview?${qs}`,
    { headers: await getAuthHeaders() },
  );
}

/**
 * Fill a slot from the asset's own prompt and files. 202 — the products land
 * in the Generated inbox as `unreviewed`, they do not join the asset.
 *
 * The arrays are normalized so a partial payload cannot crash a caller that
 * maps over `failed`; every unit failing is a 503, never a 202 over nothing.
 */
export async function generateSlot(
  scopeId: string,
  assetId: string,
  body: GenerateSlotBody,
): Promise<GenerateSlotResult> {
  const raw = await sendJson<unknown>(
    `${BASE()}/${assetId}/generate-slot?${query(scopeId)}`,
    'POST',
    body,
  );
  const out = (raw && typeof raw === 'object' ? raw : {}) as Partial<GenerateSlotResult>;
  return {
    generation_ids: Array.isArray(out.generation_ids) ? out.generation_ids : [],
    failed: Array.isArray(out.failed) ? out.failed : [],
    skipped_references: Array.isArray(out.skipped_references)
      ? out.skipped_references
      : [],
    inbox_state: 'unreviewed',
  };
}

// ─── Project refs ───────────────────────────────────────────────────────────

/**
 * Show this asset on a project's shelf. Needs WRITE access to the project.
 *
 * The write half of `listProjectAssets`, and unlike it this one DOES take a
 * `scope_id` — the ASSET's scope, which the router gates membership on. Pass
 * the row's own `scope_id` where you have a row; a scope guessed from the
 * caller's own team is how a link lands on the wrong library.
 */
export async function linkProject(
  scopeId: string,
  assetId: string,
  projectId: string,
): Promise<void> {
  await sendJson<{ linked: boolean }>(
    `${BASE()}/${assetId}/project-refs?${query(scopeId)}`,
    'POST',
    { project_id: projectId },
  );
}

/**
 * One name's outcome in an {@link importFromScript} run.
 *
 * `action` is the whole answer and the three values are NOT interchangeable:
 * `created` minted a new asset, `linked` found one already in the scope and
 * pointed the project at it, and `skipped` did NEITHER — `code`/`detail` say
 * why. Rendering only the tallies would let a refused name read as an import.
 */
export interface ImportedAssetItem {
  name: string;
  asset_type: AssetType;
  action: 'created' | 'linked' | 'skipped';
  asset_id?: string | null;
  linked?: boolean;
  code?: string | null;
  detail?: string | null;
}

/** `created + linked + skipped === items.length` always holds server-side, so
 *  the summary line needs no client-side walk of `items`. */
export interface ImportFromScriptResponse {
  items: ImportedAssetItem[];
  created: number;
  linked: number;
  skipped: number;
}

/**
 * 一键导入 — land the project's script-derived Characters and Locations as
 * assets in the project's scope and reference each from the project.
 *
 * Both types in ONE request (the endpoint reads the whole script), so a panel
 * showing only Characters still imports Locations; the per-item report is what
 * lets the caller say so rather than under-reporting its own effect.
 *
 * Like {@link listProjectAssets} and for the same reason, no `scope_id`: the
 * route derives it from the project. Re-running creates nothing.
 */
export async function importFromScript(
  projectId: string,
): Promise<ImportFromScriptResponse> {
  const raw = await sendJson<Partial<ImportFromScriptResponse>>(
    `${getApiUrl()}/api/v1/projects/${projectId}/assets/import-from-script`,
    'POST',
  );
  // `items` normalized for the same reason `normalizeDetail` normalizes its
  // arrays: a caller mapping over it must not crash on a partial payload.
  return {
    items: Array.isArray(raw?.items) ? raw.items : [],
    created: raw?.created ?? 0,
    linked: raw?.linked ?? 0,
    skipped: raw?.skipped ?? 0,
  };
}

/** The inverse of {@link linkProject}. Removes the REFERENCE only — the asset
 *  itself stays in its library, which is what the panel's copy promises. */
export async function unlinkProject(
  scopeId: string,
  assetId: string,
  projectId: string,
): Promise<void> {
  await sendJson<{ unlinked: boolean }>(
    `${BASE()}/${assetId}/project-refs/${projectId}?${query(scopeId)}`,
    'DELETE',
  );
}
