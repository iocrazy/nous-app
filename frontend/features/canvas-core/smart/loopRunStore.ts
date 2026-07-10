// features/canvas-core/smart/loopRunStore.ts
//
// Per-loop run state (Phase 1 G3b): which loops are running, whether a
// cooperative stop was requested (Infinite's runState.stopRequested), and
// a test-injectable runner override. Kept OUT of canvasCoreStore — run
// state is UI-session-local and must never dirty/persist the document.

import { create } from 'zustand';

import type { PromptCaller } from './runner';

interface LoopRunEntry {
  stopRequested: boolean;
}

interface LoopRunStore {
  running: Record<string, LoopRunEntry>;
  /** Tests (and future providers) inject the prompt caller here. */
  runnerOverride: PromptCaller | null;

  start(loopId: string): void;
  requestStop(loopId: string): void;
  finish(loopId: string): void;
  isStopRequested(loopId: string): boolean;
  setRunnerOverride(caller: PromptCaller | null): void;
  resetAll(): void;
}

export const useLoopRunStore = create<LoopRunStore>((set, get) => ({
  running: {},
  runnerOverride: null,

  start(loopId) {
    set((s) => ({ running: { ...s.running, [loopId]: { stopRequested: false } } }));
  },
  requestStop(loopId) {
    set((s) =>
      s.running[loopId]
        ? { running: { ...s.running, [loopId]: { stopRequested: true } } }
        : s,
    );
  },
  finish(loopId) {
    set((s) => {
      const { [loopId]: _done, ...rest } = s.running;
      return { running: rest };
    });
  },
  isStopRequested(loopId) {
    return get().running[loopId]?.stopRequested ?? false;
  },
  setRunnerOverride(caller) {
    set({ runnerOverride: caller });
  },
  resetAll() {
    set({ running: {}, runnerOverride: null });
  },
}));
