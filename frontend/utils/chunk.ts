/**
 * Split an array into consecutive chunks of at most `size` items.
 *
 * Used to keep PostgREST `.in(col, ids)` filters under the gateway's
 * URL/header length ceiling: a Snowflake id is ~19 chars, so a single
 * `.in()` carrying thousands of ids produces a multi-kilobyte query string
 * that Kong/nginx reject with a 502 (or 414). Chunking the id list into
 * bounded batches — run in parallel for reads, sequentially for writes —
 * keeps each request small while still covering the full set.
 *
 * Returns `[]` for an empty input (callers can `for…of` with no special case).
 */
export function chunked<T>(arr: readonly T[], size: number): T[][] {
  if (size <= 0) throw new Error(`chunk size must be > 0, got ${size}`);
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) {
    out.push(arr.slice(i, i + size));
  }
  return out;
}

/**
 * Conservative batch size for a single PostgREST `.in()` id list. 200
 * Snowflake ids (~19 chars each) ≈ 4 KB of query string — comfortably under
 * the typical 8 KB Kong/nginx header buffer, with headroom for the rest of
 * the URL. Matches the "chunk to avoid 502" pattern already used by
 * useTagSearchMap / resolveTagIntersection.
 */
export const PG_IN_CHUNK = 200;
