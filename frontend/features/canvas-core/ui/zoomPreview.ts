// features/canvas-core/ui/zoomPreview.ts
//
// IC Z-overview (smart-canvas.js enterZoomPreview/exitZoomPreview): Z
// remembers the current viewport and fits the whole graph; Z again glides
// back to where you were. Module-level — one canvas page at a time.

interface RfLike {
  getViewport(): { x: number; y: number; zoom: number };
  setViewport(v: { x: number; y: number; zoom: number }, opts?: { duration?: number }): void;
  fitView(opts?: { padding?: number; duration?: number }): void;
}

let saved: { x: number; y: number; zoom: number } | null = null;

export function toggleZoomPreview(inst: RfLike): void {
  if (saved) {
    inst.setViewport(saved, { duration: 220 });
    saved = null;
    return;
  }
  saved = inst.getViewport();
  inst.fitView({ padding: 0.12, duration: 220 });
}

export function isZoomPreview(): boolean {
  return saved !== null;
}

/** Leaving the canvas (or tests) — drop the remembered viewport. */
export function resetZoomPreview(): void {
  saved = null;
}
