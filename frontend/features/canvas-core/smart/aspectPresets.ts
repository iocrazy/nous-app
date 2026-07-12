// features/canvas-core/smart/aspectPresets.ts
//
// Image/video aspect-ratio presets — the Infinite composer's core set. Shared
// by the Prompt node's image/video controls and the Timeline director so the
// two pickers never drift apart (P2: they used to list different ratios).

export const ASPECT_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4'] as const;

export type AspectRatio = (typeof ASPECT_RATIOS)[number];
