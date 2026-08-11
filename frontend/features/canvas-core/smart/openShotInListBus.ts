/**
 * A tiny pub/sub so a BOUND shot node's "…" menu can ask whoever owns the
 * storyboard list view to switch there and locate this shot (Task 4).
 * Deletion itself never happens from the canvas — `script_shots` deletion
 * lives in the shot-list view's own inline-confirm delete (`ShotCard.tsx`);
 * this event only navigates there. Same fire-and-forget shape as
 * `promoteShotBus.ts` / `components/agentActivity/shotFocusBus.ts`: no
 * listener mounted (e.g. the standalone `/canvas/:id` route, outside the
 * storyboard page) → the request is silently dropped, which is fine.
 */

export type OpenShotInListListener = (shotId: string) => void;

const listeners = new Set<OpenShotInListListener>();

/** Subscribe; returns the unsubscribe function (useEffect-shaped). */
export function onOpenShotInList(listener: OpenShotInListListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Ask whoever is listening (Task 5's storyboard page) to switch to the
 *  shot-list view and locate this shot. No-op when nothing listens. */
export function requestOpenShotInList(shotId: string): void {
  for (const listener of [...listeners]) {
    try {
      listener(shotId);
    } catch (err) {
      console.error('[openShotInListBus] listener failed:', err);
    }
  }
}
