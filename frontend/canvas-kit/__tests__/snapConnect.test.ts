// canvas-kit/__tests__/snapConnect.test.ts
// Pure geometry for drag-snap-connect (Infinite-Canvas parity Phase 1 G1):
// probe-point containment against candidate node boxes. Mirrors Infinite's
// rectOverlapNode — inclusive bounds, no threshold, first hit in array order.

import { describe, expect, it } from 'vitest';
import { snapConnectTargetFor, type SnapBox } from '../snapConnect';

function box(id: string, x: number, y: number, width = 200, height = 120): SnapBox {
  return { id, rect: { x, y, width, height } };
}

describe('snapConnectTargetFor', () => {
  it('returns the candidate whose rect contains the probe point', () => {
    const target = box('t', 100, 100);
    const result = snapConnectTargetFor({
      draggedId: 'd',
      probePoint: { x: 150, y: 150 },
      candidates: [box('far', 1000, 1000), target],
    });
    expect(result?.id).toBe('t');
  });

  it('never returns the dragged node itself', () => {
    const result = snapConnectTargetFor({
      draggedId: 'd',
      probePoint: { x: 10, y: 10 },
      candidates: [box('d', 0, 0)],
    });
    expect(result).toBeNull();
  });

  it('returns null when the probe point is outside every candidate', () => {
    const result = snapConnectTargetFor({
      draggedId: 'd',
      probePoint: { x: 500, y: 500 },
      candidates: [box('a', 0, 0), box('b', 1000, 1000)],
    });
    expect(result).toBeNull();
  });

  it('treats rect edges as inclusive (no threshold, no margin)', () => {
    const onEdge = snapConnectTargetFor({
      draggedId: 'd',
      probePoint: { x: 200, y: 120 }, // exactly bottom-right corner of (0,0,200,120)
      candidates: [box('t', 0, 0)],
    });
    expect(onEdge?.id).toBe('t');

    const justOutside = snapConnectTargetFor({
      draggedId: 'd',
      probePoint: { x: 200.01, y: 120 },
      candidates: [box('t', 0, 0)],
    });
    expect(justOutside).toBeNull();
  });

  it('picks the first candidate in array order when several contain the point', () => {
    const result = snapConnectTargetFor({
      draggedId: 'd',
      probePoint: { x: 50, y: 50 },
      candidates: [box('first', 0, 0), box('second', 0, 0)],
    });
    expect(result?.id).toBe('first');
  });
});
