/**
 * ClassicMode per-node AbortController registry (Phase 5a B4).
 *
 * ComfyUI-style runs are long-lived, so each classic node owns at most
 * one in-flight `fetch`. This module is the single source of truth for
 * that node → AbortController mapping. It is deliberately a plain module
 * singleton (no React, no store) so:
 *   - the node view's Cancel button can abort its own request, and
 *   - the LATER cascade runner (B5) can reuse the SAME registry to abort
 *     a whole branch when an upstream node fails.
 *
 * Usage by a run kick-off:
 *   const controller = beginAbortable(nodeId);
 *   fetch(url, { signal: controller.signal });
 *   ...on settle: clearAbortController(nodeId);
 *
 * Usage by Cancel / cascade abort:
 *   abortNode(nodeId);   // aborts the signal AND clears the slot
 */

const registry = new Map<string, AbortController>();

/**
 * Start a fresh abortable run for `nodeId`. Any previous controller for
 * the same node is aborted first (a node never has two live requests).
 */
export function beginAbortable(nodeId: string): AbortController {
  registry.get(nodeId)?.abort();
  const controller = new AbortController();
  registry.set(nodeId, controller);
  return controller;
}

/** The live signal for `nodeId`, or undefined if no run is in flight. */
export function getAbortSignal(nodeId: string): AbortSignal | undefined {
  return registry.get(nodeId)?.signal;
}

/** True when a run is currently registered (in flight) for `nodeId`. */
export function hasAbortController(nodeId: string): boolean {
  return registry.has(nodeId);
}

/**
 * Abort the in-flight run for `nodeId` and drop the slot. Returns true if
 * there was something to abort, false if the node had no live run.
 */
export function abortNode(nodeId: string): boolean {
  const controller = registry.get(nodeId);
  if (!controller) return false;
  controller.abort();
  registry.delete(nodeId);
  return true;
}

/**
 * Drop the slot WITHOUT aborting — call this when a run settles normally
 * (success/failure) so the registry doesn't leak stale controllers.
 */
export function clearAbortController(nodeId: string): void {
  registry.delete(nodeId);
}

/** Test/teardown helper — abort and clear every registered controller. */
export function clearAllAbortControllers(): void {
  for (const controller of registry.values()) controller.abort();
  registry.clear();
}
