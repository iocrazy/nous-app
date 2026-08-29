// frontend/services/assetsService.ts
// Client for `/api/v1/assets` (backend/app/api/assets_router.py) — the slice
// the "Save as asset" dialog needs: search an existing asset, create a new one,
// and read one asset's loadouts. The rest of the surface (files, links,
// project refs) is deliberately not mirrored here yet; add it where it is used.
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

export interface AssetSearchOptions {
  type?: AssetType;
  q?: string;
  limit?: number;
}

/**
 * How an asset came to exist (`assets_source_check`, mirrored from
 * `app/schemas/assets.py::AssetSource`). It is provenance, written once at
 * creation and never corrected afterwards, so a caller that omits it does not
 * get "unknown" — it gets the server default `manual`, which is a claim.
 */
export type AssetSource =
  | 'manual'
  | 'script_import'
  | 'generated'
  | 'migrated'
  | 'duplicated'
  | 'system_preset';

export interface AssetCreateBody {
  asset_type: AssetType;
  name: string;
  /**
   * Defaults to `manual` SERVER-side. Any caller creating an asset on behalf
   * of a generation must pass `generated` explicitly: the backend's own
   * create-and-attach path (`generated_inbox_service.save_as_asset`) does, and
   * a two-step client that skips it writes a permanently wrong provenance
   * that nothing downstream can distinguish from a hand-made asset.
   */
  source?: AssetSource;
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
