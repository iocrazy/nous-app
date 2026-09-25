// features/canvas-core/smart/nodes/staleCatalog.ts
//
// Module-level cache for a canvas model catalog, shared by every picker on the
// page, with a staleness backstop.
//
// Why not "once per session" any more: the rows carry `last_test_status`, and
// the pickers now grey out nous-engine rows that are `idle` (authorized, not
// loaded). That status changes without any admin action — the hourly probe
// flips idle ↔ ok as the engine loads and swaps models — so a cache that lives
// until a full reload keeps a now-loaded model greyed (and a now-unloaded one
// pickable) for as long as the tab stays open. Two cheap triggers bound that:
//
//   - a mount after the cache is older than STALE_AFTER_MS refetches;
//   - the window regaining focus refetches (the user coming back from admin
//     or another tab is exactly when the answer may have moved), throttled by
//     FOCUS_MIN_GAP_MS so alt-tabbing does not become a request per switch.
//
// Stale rows keep showing while the refresh is in flight, and a failed refresh
// keeps them (logged): an empty picker would be worse than a slightly old one.
// Deliberately no polling timer — the source of truth updates hourly, and a
// picker nobody looks at does not need fresh rows.

import { useEffect, useState } from 'react';

export const STALE_AFTER_MS = 10 * 60_000;
export const FOCUS_MIN_GAP_MS = 30_000;

export interface StaleCatalog<T> {
  useRows: () => T[];
  /** Test hook: forget rows, timestamps and listeners. */
  reset: () => void;
}

export function createStaleCatalog<T>(fetchRows: () => Promise<T[]>, label: string): StaleCatalog<T> {
  let rows: T[] | null = null;
  let fetchedAt = 0;
  let inflight: Promise<void> | null = null;
  const listeners = new Set<(next: T[]) => void>();
  // Bumped by reset() so a fetch started before it cannot repopulate after it.
  let epoch = 0;

  const refresh = (): Promise<void> => {
    const started = epoch;
    inflight =
      inflight ??
      fetchRows()
        .then((next) => {
          if (started !== epoch) return;
          rows = next;
          fetchedAt = Date.now();
          listeners.forEach((notify) => notify(next));
        })
        .catch((err: unknown) => {
          console.error(`[${label}] catalog fetch failed:`, err);
        })
        .finally(() => {
          if (started === epoch) inflight = null;
        });
    return inflight;
  };

  const refreshIfOlderThan = (ms: number): void => {
    if (rows === null || Date.now() - fetchedAt > ms) void refresh();
  };

  const onFocus = (): void => refreshIfOlderThan(FOCUS_MIN_GAP_MS);

  const subscribe = (notify: (next: T[]) => void): (() => void) => {
    if (listeners.size === 0) window.addEventListener('focus', onFocus);
    listeners.add(notify);
    return () => {
      listeners.delete(notify);
      if (listeners.size === 0) window.removeEventListener('focus', onFocus);
    };
  };

  const useRows = (): T[] => {
    const [current, setCurrent] = useState<T[]>(rows ?? []);
    useEffect(() => {
      const unsubscribe = subscribe(setCurrent);
      // Rows may have landed between the first render and this effect.
      if (rows !== null) setCurrent(rows);
      refreshIfOlderThan(STALE_AFTER_MS);
      return unsubscribe;
    }, []);
    return current;
  };

  const reset = (): void => {
    epoch += 1;
    rows = null;
    fetchedAt = 0;
    inflight = null;
    listeners.clear();
    window.removeEventListener('focus', onFocus);
  };

  return { useRows, reset };
}
