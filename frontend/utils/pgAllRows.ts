/**
 * Drain a PostgREST query that must return the FULL result set, in explicit
 * `.range()` pages of 1000 — PostgREST hard-caps every response at 1000 rows,
 * so a bare `.select()` silently truncates beyond that (the root failure mode
 * of the 100k-scale audit; see backend/docs/runbook/scale-100k-readiness-audit.md).
 *
 * This is for SECONDARY views (recycle-bin folder contents, downloads sidebar)
 * whose result sets are normally modest but must never silently drop rows.
 * Primary browse paths use real keyset pagination (fetchResourcesPaginated /
 * useKeysetPagination) — do NOT use this helper there: draining 100k rows into
 * memory is its own failure.
 *
 * MAX_PAGES caps the drain at 10k rows as a deliberate safety ceiling — a
 * secondary view that exceeds it needs keyset pagination, not a bigger cap.
 *
 * The queryFactory must apply a STABLE total order (e.g. created_at DESC,
 * id DESC) or rows can shift between pages and duplicate/vanish.
 */

export const PG_PAGE = 1000;
export const MAX_PAGES = 10;

export interface PageResult<T> {
  data: T[] | null;
  error: unknown;
}

export async function fetchAllRows<T>(
  queryFactory: (from: number, to: number) => PromiseLike<PageResult<T>>,
  maxPages: number = MAX_PAGES,
): Promise<T[]> {
  const out: T[] = [];
  for (let page = 0; page < maxPages; page += 1) {
    const from = page * PG_PAGE;
    const { data, error } = await queryFactory(from, from + PG_PAGE - 1);
    if (error) throw error;
    const rows = data ?? [];
    out.push(...rows);
    if (rows.length < PG_PAGE) return out; // short page = end of the result set
  }
  // Every page came back full — the result set exceeds the ceiling. Never
  // truncate silently (the audit's core rule); the view that hits this
  // needs keyset pagination, not a bigger cap.
  console.warn(
    `[fetchAllRows] result truncated at ${out.length} rows (maxPages ceiling) — ` +
      'this view needs keyset pagination',
  );
  return out;
}
