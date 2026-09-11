/**
 * Which output the reader is pointing at (harness 3a §5).
 *
 * The rail's «Outputs» rows and the cards inside the thread sit in two
 * different React trees — the rail is a page block, the cards live deep in
 * the trajectory renderer — so a context would have to be mounted above both,
 * which is a provider the page does not have. A tiny external store keeps the
 * two in step with no provider at all, and `useSyncExternalStore` makes the
 * subscription React-correct (no stale read, no tearing).
 *
 * The key is `${kind}:${refId}:${version}` — the same key `foldEvents` gives
 * an `OutputCard` and the same shape the backend folds `seen` by.
 */
import { useSyncExternalStore } from 'react';

let highlighted: string | null = null;
const listeners = new Set<() => void>();

/** Point at one output, or `null` to clear. */
export function setHighlightedOutput(key: string | null): void {
  if (highlighted === key) return;
  highlighted = key;
  for (const fn of listeners) {
    try {
      fn();
    } catch (err) {
      // One bad subscriber never starves the others (CLAUDE.md 分发器要容纳回调异常).
      console.error('[outputHighlight] listener failed', err);
    }
  }
}

export function highlightedOutput(): string | null {
  return highlighted;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** The key being pointed at. `null` on the server snapshot — nothing is
 *  hovered during SSR, and a highlight is not worth a hydration mismatch. */
export function useHighlightedOutput(): string | null {
  return useSyncExternalStore(subscribe, highlightedOutput, () => null);
}
