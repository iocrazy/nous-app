import { useCallback, useRef, useState } from 'react';
import type { KeysetCursor } from '../services/pagination';

/**
 * One page returned by a `fetchPage` function. `totalCount` is optional and
 * only expected on the first page (cursor === null); -1 / undefined means "not
 * computed".
 */
export interface KeysetFetchResult<T> {
  data: T[];
  hasMore: boolean;
  nextCursor: KeysetCursor | null;
  totalCount?: number;
}

export type KeysetFetchPage<T> = (
  cursor: KeysetCursor | null,
  signal: AbortSignal,
) => Promise<KeysetFetchResult<T>>;

export interface KeysetPaginationState<T> {
  items: T[];
  /** Direct setter for optimistic updates / realtime patches without a reload. */
  setItems: React.Dispatch<React.SetStateAction<T[]>>;
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  error: string | null;
  totalCount: number;
  initialLoadComplete: boolean;
  /** Reset to the first page (abort any in-flight request). Call on
   *  scope/filter change and from realtime reload. */
  load: () => Promise<void>;
  /** Append the next page. No-op when already loading, no cursor, or drained. */
  loadMore: () => Promise<void>;
}

/**
 * Table-agnostic keyset (cursor) pagination state machine, extracted verbatim
 * in behaviour from the Downloads view's `useLibrary`. The caller supplies a
 * `fetchPage(cursor, signal)` that closes over its own table/scope/filters; the
 * hook owns the cursor, the abort lifecycle, and the re-entrancy guards.
 *
 * Two hard-won invariants preserved from useLibrary:
 *  - `load`/`loadMore` have STABLE identity (empty deps). All mutable state is
 *    read through `stateRef`, and the live `fetchPage` through `fetchRef`, so a
 *    state update never gives the callbacks a new identity. Without this, a
 *    consumer's "infinite scroll" effect (deps include `loadMore`) re-fires on
 *    every realtime tick and aborts the in-flight request → the historical
 *    284× AbortError storm on a single mount.
 *  - `loadMore` flips an `isLoadingMore` REF synchronously (state batches), so
 *    two synchronous scroll triggers in one tick can't double-fire.
 */
export function useKeysetPagination<T>(
  fetchPage: KeysetFetchPage<T>,
): KeysetPaginationState<T> {
  const [items, setItems] = useState<T[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState(-1);
  const [initialLoadComplete, setInitialLoadComplete] = useState(false);
  const [nextCursor, setNextCursor] = useState<KeysetCursor | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Latest fetchPage, read by the stable callbacks below (the caller usually
  // recreates it whenever scope/filters change — we must call the current one
  // without rebuilding load/loadMore).
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;

  // Mutable state mirror for the stable callbacks. Mutated synchronously so the
  // re-entrancy guard in loadMore is reliable despite React state batching.
  const stateRef = useRef({ nextCursor, hasMore, isLoadingMore });
  stateRef.current = { nextCursor, hasMore, isLoadingMore };

  const isAbort = (err: unknown, signal: AbortSignal): boolean => {
    const e = err as { name?: string; message?: string } | undefined;
    return (
      e?.name === 'AbortError' ||
      signal.aborted ||
      (typeof e?.message === 'string' && e.message.includes('aborted'))
    );
  };

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    setError(null);
    setNextCursor(null);
    setHasMore(true);
    try {
      const result = await fetchRef.current(null, controller.signal);
      if (controller.signal.aborted) return;
      setItems(result.data);
      setHasMore(result.hasMore);
      setNextCursor(result.nextCursor);
      if (result.totalCount != null && result.totalCount >= 0) {
        setTotalCount(result.totalCount);
      }
    } catch (err) {
      if (isAbort(err, controller.signal)) return;
      console.error('Keyset load failed:', err);
      setItems([]);
      setHasMore(false);
      const e = err as { message?: string };
      setError(e?.message || String(err));
    } finally {
      if (!controller.signal.aborted) {
        setIsLoading(false);
        setInitialLoadComplete(true);
      }
    }
  // Stable identity: state read via stateRef, fetchPage via fetchRef.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadMore = useCallback(async () => {
    const s = stateRef.current;
    if (!s.hasMore || s.isLoadingMore) return;
    if (!s.nextCursor) {
      // No cursor → nothing more (or first load not done). Never fall back to
      // offset — that would re-fetch page 1.
      setHasMore(false);
      return;
    }
    stateRef.current.isLoadingMore = true;
    setIsLoadingMore(true);
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    try {
      const result = await fetchRef.current(s.nextCursor, controller.signal);
      if (controller.signal.aborted) return;
      if (result.data.length > 0) {
        setItems((prev) => [...prev, ...result.data]);
        setNextCursor(result.nextCursor);
        setHasMore(result.hasMore);
      } else {
        setHasMore(false);
        setNextCursor(null);
      }
    } catch (err) {
      if (isAbort(err, controller.signal)) return;
      console.error('Keyset loadMore failed:', err);
    } finally {
      stateRef.current.isLoadingMore = false;
      setIsLoadingMore(false);
    }
  // Stable identity — see load().
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    items,
    setItems,
    isLoading,
    isLoadingMore,
    hasMore,
    error,
    totalCount,
    initialLoadComplete,
    load,
    loadMore,
  };
}
