// canvas-kit/snapConnect.ts
//
// Pure geometry for drag-snap-connect (Infinite-Canvas parity Phase 1 G1):
// dragging a node so its probe point lands inside another node's box offers
// an automatic connection. Mirrors Infinite's rectOverlapNode semantics —
// point containment with inclusive bounds, no threshold/hysteresis, first
// hit in candidate array order wins. The engine decides the probe point
// (node center, or the pointer for prompt/loop-style sources) and validates
// the pair; this module only answers "which box is under the probe?".

export interface SnapBox {
  id: string;
  rect: { x: number; y: number; width: number; height: number };
}

export function snapConnectTargetFor(args: {
  draggedId: string;
  probePoint: { x: number; y: number };
  candidates: SnapBox[];
}): SnapBox | null {
  const { draggedId, probePoint, candidates } = args;
  for (const candidate of candidates) {
    if (candidate.id === draggedId) continue;
    const { x, y, width, height } = candidate.rect;
    if (
      probePoint.x >= x &&
      probePoint.x <= x + width &&
      probePoint.y >= y &&
      probePoint.y <= y + height
    ) {
      return candidate;
    }
  }
  return null;
}
