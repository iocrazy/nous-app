/**
 * Bounded concurrency pool utility.
 *
 * Runs `worker` over every item in `items` with at most `limit` workers in
 * flight at the same time.  Results are returned in INPUT ORDER regardless of
 * completion order.  The outer promise never rejects: per-item errors are
 * wrapped as `{ ok: false, error }`.
 *
 * AbortSignal behaviour
 * ---------------------
 * When `opts.signal` is already aborted (or becomes aborted between items),
 * not-yet-started items are NOT passed to `worker`.  Instead each such slot
 * returns `{ ok: false, error: DOMException('AbortError') }`.  Already-running
 * workers are allowed to finish naturally (the signal is not forwarded to them
 * — pass it via your worker closure if you need that).
 */
export async function runWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
  opts?: { signal?: AbortSignal },
): Promise<Array<{ ok: true; value: R } | { ok: false; error: unknown }>> {
  if (items.length === 0) return [];

  const results: Array<{ ok: true; value: R } | { ok: false; error: unknown }> =
    new Array(items.length);
  const signal = opts?.signal;

  // Shared index: each pool slot atomically claims the next available item by
  // incrementing this counter.  Safe because JS is single-threaded — the
  // read-then-increment crosses no await boundary.
  let nextIndex = 0;

  async function poolSlot(): Promise<void> {
    while (nextIndex < items.length) {
      const i = nextIndex++;

      if (signal?.aborted) {
        // Do not call worker; mark slot as aborted and move on so the
        // remaining items are also claimed and marked without blocking.
        results[i] = {
          ok: false,
          error: new DOMException('The operation was aborted.', 'AbortError'),
        };
        continue;
      }

      try {
        const value = await worker(items[i], i);
        results[i] = { ok: true, value };
      } catch (error) {
        results[i] = { ok: false, error };
      }
    }
  }

  // Spawn min(limit, items.length) pool slots in parallel.
  const poolSize = Math.min(limit, items.length);
  await Promise.all(Array.from({ length: poolSize }, poolSlot));

  return results;
}
