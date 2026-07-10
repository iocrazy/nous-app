// canvas-kit/__tests__/arrangeLayout.test.ts
// Topology auto-arrange (Infinite-Canvas parity G6 — arrangeSelectedCanvasNodes):
// dagre left-to-right layout over the current graph, honoring measured node
// sizes. Pure — returns NEW node objects with updated positions.

import { describe, expect, it } from 'vitest';
import { arrangeLayout } from '../arrangeLayout';

const node = (id: string, x = 0, y = 0, w?: number, h?: number) => ({
  id,
  position: { x, y },
  ...(w != null ? { measured: { width: w, height: h ?? 100 } } : {}),
});
const edge = (source: string, target: string) => ({ id: `${source}-${target}`, source, target });

describe('arrangeLayout', () => {
  it('lays a chain out left-to-right by rank', () => {
    const out = arrangeLayout(
      [node('a'), node('b'), node('c')],
      [edge('a', 'b'), edge('b', 'c')],
    );
    const x = Object.fromEntries(out.map((n) => [n.id, n.position.x]));
    expect(x.a).toBeLessThan(x.b);
    expect(x.b).toBeLessThan(x.c);
    expect(out.every((n) => Number.isFinite(n.position.x) && Number.isFinite(n.position.y))).toBe(
      true,
    );
  });

  it('keeps disconnected nodes placed (no NaN, no overlap with the chain rank 0)', () => {
    const out = arrangeLayout([node('a'), node('lonely')], [edge('a', 'a-x')]);
    expect(out).toHaveLength(2);
    expect(out.every((n) => Number.isFinite(n.position.x))).toBe(true);
  });

  it('returns new objects and does not mutate the input', () => {
    const input = [node('a', 5, 5), node('b', 6, 6)];
    const out = arrangeLayout(input, [edge('a', 'b')]);
    expect(input[0].position).toEqual({ x: 5, y: 5 });
    expect(out[0]).not.toBe(input[0]);
  });

  it('preserves the graph centroid (a graph living at (3000, 2000) stays there)', () => {
    const input = [node('a', 2900, 1900), node('b', 3100, 2100)];
    const out = arrangeLayout(input, [edge('a', 'b')]);
    const cx = (out[0].position.x + out[1].position.x) / 2;
    const cy = (out[0].position.y + out[1].position.y) / 2;
    expect(cx).toBeCloseTo(3000, 0);
    expect(cy).toBeCloseTo(2000, 0);
  });

  it('uses measured sizes for spacing (bigger nodes push ranks further apart)', () => {
    const small = arrangeLayout([node('a', 0, 0, 100, 50), node('b')], [edge('a', 'b')]);
    const large = arrangeLayout([node('a', 0, 0, 500, 50), node('b')], [edge('a', 'b')]);
    const gapSmall = small[1].position.x - small[0].position.x;
    const gapLarge = large[1].position.x - large[0].position.x;
    expect(gapLarge).toBeGreaterThan(gapSmall);
  });
});
