// features/canvas-core/ui/pointerWorld.ts
//
// IC lastMouseWorld: the pointer's most recent position in FLOW coords,
// recorded by CanvasSurface on every pointer move over the canvas and
// consumed by paste (subgraph centres on the pointer instead of a blind
// cascading offset). Module-level singleton — one canvas page at a time.

let last: { x: number; y: number } | null = null;

export function setPointerWorld(pos: { x: number; y: number } | null): void {
  last = pos;
}

export function getPointerWorld(): { x: number; y: number } | null {
  return last;
}
