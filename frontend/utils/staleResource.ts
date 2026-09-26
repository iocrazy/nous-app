// frontend/utils/staleResource.ts
//
// A module-level cache for one fetched value, shared by every component that
// reads it, with a staleness backstop. Moved from the canvas catalogs
// (features/canvas-core/smart/nodes/staleCatalog) when the platform model list
// moved onto the AI settings (spec 2026-09-25): the one thing still fetched on
// its own is the platform RUNTIME state (hooks/usePlatformStatus), and it keeps
// exactly the policy the catalogs had.
//
// Why not "once per session": the value changes without any user action —
// nous-engine loads and swaps models, a daemon goes offline — so a cache that
// lives until a full reload keeps a now-loaded model greyed (and a now-unloaded
// one pickable) for as long as the tab stays open. Two cheap triggers bound it:
//
//   - a mount after the cache is older than STALE_AFTER_MS refetches;
//   - the window regaining focus refetches (the user coming back from admin
//     or another tab is exactly when the answer may have moved), throttled by
//     FOCUS_MIN_GAP_MS so alt-tabbing does not become a request per switch.
//
// The old value keeps showing while a refresh is in flight, and a failed
// refresh keeps it (logged): an empty picker would be worse than a slightly
// old one. Deliberately no polling timer — a picker nobody looks at does not
// need a fresh answer.

import { useEffect, useState } from 'react';

export const STALE_AFTER_MS = 10 * 60_000;
export const FOCUS_MIN_GAP_MS = 30_000;

export interface StaleResource<T> {
  /** The latest value, or null until the first fetch succeeds. */
  useValue: () => T | null;
  /** Test hook: forget the value, timestamps and listeners. */
  reset: () => void;
}

export function createStaleResource<T>(
  fetchValue: () => Promise<T>,
  label: string,
): StaleResource<T> {
  let value: T | null = null;
  let fetchedAt = 0;
  let inflight: Promise<void> | null = null;
  const listeners = new Set<(next: T) => void>();
  // Bumped by reset() so a fetch started before it cannot repopulate after it.
  let epoch = 0;

  const refresh = (): Promise<void> => {
    const started = epoch;
    inflight =
      inflight ??
      // Promise.resolve().then: a fetcher that throws synchronously is logged
      // like any other failure instead of escaping into a React effect.
      Promise.resolve()
        .then(fetchValue)
        .then((next) => {
          if (started !== epoch) return;
          value = next;
          fetchedAt = Date.now();
          listeners.forEach((notify) => notify(next));
        })
        .catch((err: unknown) => {
          console.error(`[${label}] fetch failed:`, err);
        })
        .finally(() => {
          if (started === epoch) inflight = null;
        });
    return inflight;
  };

  const refreshIfOlderThan = (ms: number): void => {
    if (value === null || Date.now() - fetchedAt > ms) void refresh();
  };

  const onFocus = (): void => refreshIfOlderThan(FOCUS_MIN_GAP_MS);

  const subscribe = (notify: (next: T) => void): (() => void) => {
    if (listeners.size === 0) window.addEventListener('focus', onFocus);
    listeners.add(notify);
    return () => {
      listeners.delete(notify);
      if (listeners.size === 0) window.removeEventListener('focus', onFocus);
    };
  };

  const useValue = (): T | null => {
    const [current, setCurrent] = useState<T | null>(value);
    useEffect(() => {
      const unsubscribe = subscribe(setCurrent);
      // The value may have landed between the first render and this effect.
      if (value !== null) setCurrent(value);
      refreshIfOlderThan(STALE_AFTER_MS);
      return unsubscribe;
    }, []);
    return current;
  };

  const reset = (): void => {
    epoch += 1;
    value = null;
    fetchedAt = 0;
    inflight = null;
    listeners.clear();
    window.removeEventListener('focus', onFocus);
  };

  return { useValue, reset };
}
