/**
 * A tiny pub/sub so an unbound shot node's "Promote to Shot" menu entry can
 * reach whoever owns the binding flow — Task 4 wires the subscriber (creates
 * a `script_shots` row + patches `shot_id`/mirror fields onto this node).
 * Same module-level-bus shape as `components/agentActivity/shotFocusBus.ts`
 * (fire-and-forget: no listener mounted → the request is dropped, which is
 * fine — Task 3 only needs the event to exist and fire).
 */

export type PromoteShotListener = (nodeId: string) => void;

const listeners = new Set<PromoteShotListener>();

/** Subscribe; returns the unsubscribe function (useEffect-shaped). */
export function onPromoteShot(listener: PromoteShotListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Ask whoever is listening to promote this node into a bound shot. No-op
 *  when nothing listens (Task 4 not wired yet). */
export function requestPromoteShot(nodeId: string): void {
  for (const listener of [...listeners]) {
    try {
      listener(nodeId);
    } catch (err) {
      console.error('[promoteShotBus] listener failed:', err);
    }
  }
}
