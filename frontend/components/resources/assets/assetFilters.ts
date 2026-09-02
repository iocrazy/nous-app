// frontend/components/resources/assets/assetFilters.ts
//
// URL search-params ↔ asset-shelf filter state. Pure: no React, no service
// calls, so every rule below is testable without a DOM.
//
// The shelf's view state is split across two halves of the URL, and the split
// is deliberate:
//
//   * the TYPE is a path segment (`resources/assets/:assetType`) — it is what
//     the sidebar navigates to and what the redirect guard validates, so it
//     cannot also live here;
//   * everything that NARROWS one type's shelf (project / readiness / tag) and
//     the sort order live in the query string, and are carried across a tab
//     change unchanged. Filtering to a project and then switching from
//     Characters to Locations should keep the project.
//
// Param names mirror the wire (`project_id`, `readiness`, `tag`, `sort`) so a
// URL reads the same as the request it produces.
//
// This module intentionally does NOT re-export `generatedFilters`' `nonEmpty` /
// `oneOf`: the two shelves answer to different routers with different
// enumerations, and sharing the vocabulary helpers would invite sharing the
// vocabularies next.

import type {
  AssetLibraryFilter,
  AssetListOptions,
  AssetType,
} from '../../../services/assetsService';

// ─── Vocabularies (mirrored from `assets_router.list_assets`) ───────────────
//
// Each of these is a server-side `pattern=` on the query param. A value the
// server does not know is a 422, not an unfiltered shelf that looks filtered —
// which is exactly why unknown values are dropped here rather than forwarded.

export const READINESS_VALUES = ['ready', 'draft'] as const;
export type ReadinessFilter = (typeof READINESS_VALUES)[number];

export const SORT_VALUES = ['recent', 'name', 'readiness'] as const;
export type AssetSort = (typeof SORT_VALUES)[number];

/** Library membership (mig 449). Mirrors `assets_router.list_assets`'s
 *  `library` param — see `AssetLibraryFilter` for what each value means. */
export const LIBRARY_VALUES = ['in', 'out', 'all'] as const;

/** The router's own default: the shelf IS the library. Serializing it would
 *  put a param in the URL that produces the identical request, so like
 *  `DEFAULT_SORT` it is omitted rather than written. */
export const DEFAULT_LIBRARY: AssetLibraryFilter = 'in';

/** The router's own default. Serializing it would put a param in the URL that
 *  produces the identical request — so it is omitted, not written. */
export const DEFAULT_SORT: AssetSort = 'recent';

/** One page. Matches `list_assets(limit=60)`; the cap is 200. Kept explicit
 *  rather than left to the server default because "Load More" needs to know
 *  the page size to tell a full page from a last one. */
export const ASSET_PAGE_SIZE = 60;

// ─── State ──────────────────────────────────────────────────────────────────

export interface AssetFilters {
  projectId: string | null;
  readiness: ReadinessFilter | null;
  /**
   * Which side of `in_library` the shelf shows. NOT nullable, unlike the other
   * three: "no library filter" is not a state this shelf has — every request
   * carries one of the three values, and `in` is simply the default. A null
   * here would be a fourth value meaning the same as `in`, which is how a
   * default becomes impossible to change.
   */
  library: AssetLibraryFilter;
  /** One tag VALUE (not `group:value`) — the server matches it across every
   *  tag group, which is what `tags` being a jsonb object of group → values
   *  makes possible. */
  tag: string | null;
  sort: AssetSort;
}

export function defaultAssetFilters(): AssetFilters {
  return {
    projectId: null,
    readiness: null,
    library: DEFAULT_LIBRARY,
    tag: null,
    sort: DEFAULT_SORT,
  };
}

// ─── Parse / serialize ──────────────────────────────────────────────────────

/** A param that is present but empty carries no information — treat it as
 *  absent rather than as a filter matching the empty string. */
function nonEmpty(value: string | null): string | null {
  const trimmed = value?.trim() ?? '';
  return trimmed === '' ? null : trimmed;
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  const candidate = nonEmpty(value);
  return candidate !== null && (allowed as readonly string[]).includes(candidate)
    ? (candidate as T)
    : null;
}

/**
 * Read filter state out of the URL. Unknown values are DROPPED, never
 * forwarded: the query string is user-editable, and passing `sort=cheapest`
 * or `readiness=maybe` straight through would earn a 422 the user reads as a
 * broken page rather than a bad link.
 */
export function parseAssetFilters(searchParams: URLSearchParams): AssetFilters {
  return {
    projectId: nonEmpty(searchParams.get('project_id')),
    readiness: oneOf(searchParams.get('readiness'), READINESS_VALUES),
    library: oneOf(searchParams.get('library'), LIBRARY_VALUES) ?? DEFAULT_LIBRARY,
    tag: nonEmpty(searchParams.get('tag')),
    sort: oneOf(searchParams.get('sort'), SORT_VALUES) ?? DEFAULT_SORT,
  };
}

/** The inverse. Defaults are omitted so the landing URL stays bare. */
export function serializeAssetFilters(filters: AssetFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (filters.projectId) sp.set('project_id', filters.projectId);
  if (filters.readiness) sp.set('readiness', filters.readiness);
  if (filters.library !== DEFAULT_LIBRARY) sp.set('library', filters.library);
  if (filters.tag) sp.set('tag', filters.tag);
  if (filters.sort !== DEFAULT_SORT) sp.set('sort', filters.sort);
  return sp;
}

// ─── Derived ────────────────────────────────────────────────────────────────

/**
 * True when something is NARROWING the shelf — which is what separates "you
 * have no characters" from "none of your characters match this filter".
 *
 * `sort` is excluded on purpose: re-ordering a shelf never removes a row from
 * it, so a sorted-but-unfiltered empty shelf is genuinely empty.
 *
 * `library` counts only as `out`. `in` is the DEFAULT view — an empty shelf
 * there is the honest "you have no characters yet", and reporting it as
 * "nothing matches your filters" would send the user hunting for a filter they
 * never set. `all` is the WIDEST possible query, so an empty result under it
 * cannot have been narrowed by anything.
 */
export function hasActiveAssetFilters(filters: AssetFilters): boolean {
  return (
    filters.projectId !== null ||
    filters.readiness !== null ||
    filters.library === 'out' ||
    filters.tag !== null
  );
}

/**
 * Filter state (+ the tab's type, + a page window) → `listAssets` options.
 *
 * Absent filters are left OFF the object rather than sent as `null`, so "not
 * filtered" and "filtered to the router's own default" produce the same
 * request. `offset` is passed through even when 0 — `listAssets` only drops
 * `undefined`, and 0 is a meaningful first page.
 */
export function assetListOptionsFor(
  filters: AssetFilters,
  assetType: AssetType | null,
  offset = 0,
  limit = ASSET_PAGE_SIZE,
): AssetListOptions {
  const opts: AssetListOptions = { limit, offset };
  if (assetType) opts.type = assetType;
  if (filters.projectId) opts.projectId = filters.projectId;
  if (filters.readiness) opts.readiness = filters.readiness;
  if (filters.library !== DEFAULT_LIBRARY) opts.library = filters.library;
  if (filters.tag) opts.tag = filters.tag;
  if (filters.sort !== DEFAULT_SORT) opts.sort = filters.sort;
  return opts;
}

/**
 * Every tag VALUE present on a page of rows, sorted, plus whatever value is
 * currently filtered on.
 *
 * The options come from WHAT IS ON SCREEN, like the Generated inbox's model
 * chip: there is no endpoint that enumerates a scope's tag vocabulary, and
 * inventing one would advertise filters that match nothing. Including the
 * active value keeps a filtered-to-empty shelf clearable — without it the only
 * option in the popover would be "Any Tag", and the chip would not show what
 * it is filtering by.
 *
 * `tags` is a jsonb OBJECT of group → values (`{"role": ["lead"]}`), so the
 * values are gathered out of the object's arrays; a scalar value is taken as
 * a single value rather than dropped.
 */
export function tagOptionsFrom(
  rows: ReadonlyArray<{ tags: Record<string, unknown> }>,
  active: string | null,
): string[] {
  const seen = new Set<string>();
  for (const row of rows) {
    for (const group of Object.values(row.tags ?? {})) {
      if (Array.isArray(group)) {
        for (const value of group) if (typeof value === 'string' && value) seen.add(value);
      } else if (typeof group === 'string' && group) {
        seen.add(group);
      }
    }
  }
  if (active) seen.add(active);
  return [...seen].sort();
}
