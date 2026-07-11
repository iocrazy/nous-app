// canvas-kit/knifeStore.ts
// Knife-mode runtime toggle (view state, never persisted). A tiny zustand
// store so the window-scoped shortcut layer, the composer button, and the
// surface overlay share it without prop drilling.

import { create } from 'zustand';

interface KnifeState {
  active: boolean;
  toggle(): void;
  exit(): void;
}

export const useKnifeStore = create<KnifeState>((set) => ({
  active: false,
  toggle: () => set((s) => ({ active: !s.active })),
  exit: () => set({ active: false }),
}));
