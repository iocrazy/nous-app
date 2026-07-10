// features/canvas-core/smart/regenStore.ts
// Runtime state for slot regeneration (G7) — which output nodes are
// re-running right now, plus a runner override for tests. Mirrors
// loopRunStore: view-only state, never persisted with the canvas.

import { create } from 'zustand';

import type { PromptCaller } from './runner';

/** Node ids are only unique within one canvas (counter-based factory ids) —
 *  key runtime state by canvas too so an in-flight regen on canvas A never
 *  disables a same-id node's button on canvas B. */
export function regenKey(canvasId: string | null, nodeId: string): string {
  return `${canvasId ?? ''}:${nodeId}`;
}

interface RegenState {
  /** regenKey(canvasId, outputNodeId) → true while a regenerate is in flight. */
  running: Record<string, boolean>;
  /** Test seam — replaces the backend generation runner. */
  runnerOverride: PromptCaller | null;
  start(id: string): void;
  finish(id: string): void;
  reset(): void;
}

export const useRegenStore = create<RegenState>((set) => ({
  running: {},
  runnerOverride: null,
  start: (id) => set((s) => ({ running: { ...s.running, [id]: true } })),
  finish: (id) =>
    set((s) => {
      const { [id]: _done, ...rest } = s.running;
      return { running: rest };
    }),
  reset: () => set({ running: {}, runnerOverride: null }),
}));
