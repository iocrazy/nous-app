/**
 * Shared keyset (cursor) pagination engine.
 *
 * Extracted from the Downloads view's `fetchLibraryPaginated` so every large
 * list (Downloads, the Resources library, smart folders, the recycle bin) can
 * page the SAME way instead of bulk-fetching everything — which silently caps
 * at PostgREST's 1000-row ceiling once a table exceeds 1000 rows.
 *
 * Keyset beats OFFSET here: it is O(1) regardless of scroll depth, and — with
 * the `id` tiebreaker — it does not lose rows when many share one `created_at`
 * (e.g. a batch insert at a single crawl tick). Dropping the tiebreaker is what
 * caused the historical 417→340 "missing rows" bug.
 *
 * Contract: the caller's query MUST be ordered `(<tsCol> DESC, <idCol> DESC)`
 * and select both columns so a row can yield the next cursor.
 */

/** Opaque forward cursor: the (timestamp, id) of the last row of a page. */
export interface KeysetCursor {
  ts: string;
  id: string;
}

/** One page of a keyset scan. */
export interface KeysetPage<T> {
  data: T[];
  hasMore: boolean;
  nextCursor: KeysetCursor | null;
}

/** A page plus the total row count (computed once, on the first page). */
export interface KeysetListPage<T> extends KeysetPage<T> {
  totalCount: number;
}

/**
 * supabase-js query builders are deeply generic; we only need the two methods
 * we call here, so accept anything chainable and hand it straight back.
 */
type Chainable = {
  or: (filter: string) => Chainable;
  limit: (n: number) => Chainable;
};

export interface KeysetColumns {
  /** Timestamp column the query is ordered by (descending). Default `created_at`. */
  tsCol?: string;
  /** Unique tiebreaker column (descending). Default `id`. */
  idCol?: string;
}

/**
 * Apply the keyset WHERE + the `pageSize + 1` probe-limit to a query already
 * ordered `(tsCol DESC, idCol DESC)`. Pass `cursor=null` for the first page.
 *
 * The PostgREST encoding of `(tsCol, idCol) < (cursor.ts, cursor.id)` is
 * `or(tsCol.lt.<ts>, and(tsCol.eq.<ts>, idCol.lt.<id>))`. Fetching one extra
 * row (`pageSize + 1`) is how `sliceKeysetPage` detects `hasMore` without a
 * second round-trip.
 */
export function applyKeysetCursor<Q extends Chainable>(
  query: Q,
  cursor: KeysetCursor | null,
  pageSize: number,
  cols: KeysetColumns = {},
): Q {
  const tsCol = cols.tsCol ?? 'created_at';
  const idCol = cols.idCol ?? 'id';
  let q = query;
  if (cursor) {
    q = q.or(
      `${tsCol}.lt.${cursor.ts},and(${tsCol}.eq.${cursor.ts},${idCol}.lt.${cursor.id})`,
    ) as Q;
  }
  return q.limit(pageSize + 1) as Q;
}

/**
 * Split raw rows (up to `pageSize + 1`) into the visible page + the next
 * cursor. `cursorOf` extracts `{ts, id}` from a row — its location differs per
 * query (e.g. `created_at` may belong to `resources`, not the embedded join),
 * so the caller supplies it and reads it BEFORE any row-flattening overwrites
 * those fields.
 */
export function sliceKeysetPage<T>(
  rows: T[],
  pageSize: number,
  cursorOf: (row: T) => KeysetCursor | null,
): KeysetPage<T> {
  const hasMore = rows.length > pageSize;
  const data = hasMore ? rows.slice(0, pageSize) : rows;
  const last = data.length > 0 ? data[data.length - 1] : undefined;
  const nextCursor = hasMore && last ? cursorOf(last) : null;
  return { data, hasMore, nextCursor };
}
