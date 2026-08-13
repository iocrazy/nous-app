import { describe, expect, it } from 'vitest';

import {
  IDENTITY_VIEWPORT,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  panByScreenDelta,
  screenToWorld,
  viewportFramesAnyNode,
  visibleWorldRect,
  worldToScreen,
  zoomAroundScreenAnchor,
} from './viewport';

const FLOAT_TOL = 1e-9;

describe('worldToScreen / screenToWorld', () => {
  it('identity viewport leaves coordinates unchanged', () => {
    const p = { x: 42, y: -17 };
    expect(worldToScreen(p, IDENTITY_VIEWPORT)).toEqual(p);
    expect(screenToWorld(p, IDENTITY_VIEWPORT)).toEqual(p);
  });

  it('zoom scales around the origin', () => {
    const viewport = { x: 0, y: 0, zoom: 2 };
    expect(worldToScreen({ x: 10, y: 5 }, viewport)).toEqual({ x: 20, y: 10 });
  });

  it('offset adds in screen space, AFTER zoom', () => {
    const viewport = { x: 100, y: 50, zoom: 2 };
    expect(worldToScreen({ x: 10, y: 5 }, viewport)).toEqual({ x: 120, y: 60 });
  });

  it('round-trip is identity', () => {
    const viewport = { x: 137, y: -42, zoom: 1.7 };
    const world = { x: 91, y: 13 };
    const there = worldToScreen(world, viewport);
    const back = screenToWorld(there, viewport);
    expect(back.x).toBeCloseTo(world.x, 9);
    expect(back.y).toBeCloseTo(world.y, 9);
  });

  it('zero or negative zoom is treated as identity (no Infinity)', () => {
    const broken = { x: 10, y: 20, zoom: 0 };
    const p = { x: 5, y: 5 };
    expect(screenToWorld(p, broken)).toEqual({ x: -5, y: -15 });
    expect(worldToScreen(p, broken)).toEqual({ x: 15, y: 25 });
  });

  it('NaN zoom falls back to identity', () => {
    const broken = { x: 0, y: 0, zoom: Number.NaN };
    expect(worldToScreen({ x: 3, y: 4 }, broken)).toEqual({ x: 3, y: 4 });
  });
});

describe('zoomAroundScreenAnchor', () => {
  it('keeps the world point under the anchor invariant', () => {
    const before = { x: 50, y: 30, zoom: 1 };
    const anchor = { x: 200, y: 150 };
    const worldBefore = screenToWorld(anchor, before);

    const after = zoomAroundScreenAnchor(before, anchor, 2);
    const worldAfter = screenToWorld(anchor, after);

    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 9);
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 9);
    expect(after.zoom).toBe(2);
  });

  it('round-trips through wheel-up then wheel-down at same anchor', () => {
    const v0 = { x: 10, y: 20, zoom: 1 };
    const anchor = { x: 400, y: 300 };
    const up = zoomAroundScreenAnchor(v0, anchor, 1.5);
    const down = zoomAroundScreenAnchor(up, anchor, 1);
    expect(down.x).toBeCloseTo(v0.x, 9);
    expect(down.y).toBeCloseTo(v0.y, 9);
    expect(down.zoom).toBeCloseTo(v0.zoom, 9);
  });

  it('no-op when next zoom == current zoom', () => {
    const v = { x: 33, y: 44, zoom: 1.7 };
    expect(zoomAroundScreenAnchor(v, { x: 0, y: 0 }, 1.7)).toBe(v);
  });
});

describe('panByScreenDelta', () => {
  it('shifts offset, leaves zoom alone', () => {
    const v = { x: 10, y: 20, zoom: 1.5 };
    const next = panByScreenDelta(v, 3, -7);
    expect(next).toEqual({ x: 13, y: 13, zoom: 1.5 });
  });

  it('is a pure function (does not mutate input)', () => {
    const v = { x: 0, y: 0, zoom: 1 };
    panByScreenDelta(v, 5, 5);
    expect(v).toEqual({ x: 0, y: 0, zoom: 1 });
  });
});

describe('clampZoom', () => {
  it('passes through values inside range', () => {
    expect(clampZoom(1.5)).toBe(1.5);
  });

  it('clamps both ends', () => {
    expect(clampZoom(0.001)).toBe(MIN_ZOOM);
    expect(clampZoom(100)).toBe(MAX_ZOOM);
  });

  it('falls back to 1 on NaN / Infinity (non-finite is unsafe)', () => {
    expect(clampZoom(Number.NaN)).toBe(1);
    expect(clampZoom(Number.POSITIVE_INFINITY)).toBe(1);
    expect(clampZoom(Number.NEGATIVE_INFINITY)).toBe(1);
  });
});

// ============================================================
// Empty-viewport self-heal (2026-08-12 production incident)
// ============================================================

/** Canvas 337610660408263 as it really is in production: the saved viewport
 *  frames world x≈-352..2138 on the ~1280×720 surface it was saved from,
 *  while every de-duplicated shot node sits in one column at x=2240. */
const PROD_VIEWPORT = { x: 181.47, y: 106.68, zoom: 0.514 };
const PROD_SIZE = { width: 1280, height: 720 };
const PROD_SHOT_NODES = [0, 400, 800, 1200, 1600, 2000].map((y, i) => ({
  id: `shot-33761066040830${i}`,
  type: 'shot',
  position: { x: 2240, y },
  data: {},
}));

describe('visibleWorldRect', () => {
  it('inverts the viewport transform (screen box → world box)', () => {
    const rect = visibleWorldRect({ x: 100, y: 50, zoom: 2 }, { width: 800, height: 600 });
    expect(rect).toEqual({ x: -50, y: -25, width: 400, height: 300 });
  });

  it('treats a 0 / non-finite zoom as identity rather than producing Infinity', () => {
    const rect = visibleWorldRect({ x: 0, y: 0, zoom: 0 }, { width: 800, height: 600 });
    // (`-0` for the offsets — negating zero; identical arithmetically.)
    expect(rect.width).toBe(800);
    expect(rect.height).toBe(600);
    expect(rect.x).toBeCloseTo(0);
    expect(rect.y).toBeCloseTo(0);
  });
});

describe('viewportFramesAnyNode', () => {
  it('the production row: the saved viewport frames NONE of the shot nodes', () => {
    const rect = visibleWorldRect(PROD_VIEWPORT, PROD_SIZE);
    // Right edge of the visible world rect ≈ 2138; the column starts at 2240.
    expect(rect.x + rect.width).toBeLessThan(2240);
    expect(viewportFramesAnyNode(PROD_VIEWPORT, PROD_SHOT_NODES, PROD_SIZE)).toBe(false);
  });

  it('one node inside the rect is enough to call the viewport good', () => {
    const nodes = [...PROD_SHOT_NODES, { id: 'shot-in-view', position: { x: 0, y: 0 } }];
    expect(viewportFramesAnyNode(PROD_VIEWPORT, nodes, PROD_SIZE)).toBe(true);
  });

  it('a node merely CLIPPED by the rect edge counts as framed (overlap, not containment)', () => {
    // Default 200×120 box straddling the right edge: position 2100 < 2138.
    const clipped = [{ id: 'shot-clipped', position: { x: 2100, y: 0 } }];
    expect(viewportFramesAnyNode(PROD_VIEWPORT, clipped, PROD_SIZE)).toBe(true);
    // …one box-width further out no longer overlaps at all.
    const outside = [{ id: 'shot-outside', position: { x: 2139, y: 0 } }];
    expect(viewportFramesAnyNode(PROD_VIEWPORT, outside, PROD_SIZE)).toBe(false);
  });

  it('an explicit size (a group\'s style box) is used instead of the default', () => {
    // Top-left far left of the rect, but 3000 wide → it reaches into view.
    const wide = [
      { id: 'group-1', position: { x: -3000, y: 0 }, style: { width: 3000, height: 200 } },
    ];
    expect(viewportFramesAnyNode(PROD_VIEWPORT, wide, PROD_SIZE)).toBe(true);
    const narrow = [
      { id: 'group-2', position: { x: -3000, y: 0 }, style: { width: 100, height: 200 } },
    ];
    expect(viewportFramesAnyNode(PROD_VIEWPORT, narrow, PROD_SIZE)).toBe(false);
  });

  it('an empty canvas is always "framed" — nothing to heal', () => {
    expect(viewportFramesAnyNode(PROD_VIEWPORT, [], PROD_SIZE)).toBe(true);
  });

  it('nodes with no usable position claim "framed" (never fit on no evidence)', () => {
    expect(
      viewportFramesAnyNode(PROD_VIEWPORT, [{ id: 'a' }, { id: 'b', position: null }], PROD_SIZE),
    ).toBe(true);
  });

  it('the identity viewport frames a node at the origin', () => {
    expect(
      viewportFramesAnyNode(IDENTITY_VIEWPORT, [{ id: 'n1', position: { x: 0, y: 0 } }], PROD_SIZE),
    ).toBe(true);
  });

  it('vertical misses count too (a column far above the visible band)', () => {
    const above = [{ id: 'n1', position: { x: 0, y: -100000 } }];
    expect(viewportFramesAnyNode(IDENTITY_VIEWPORT, above, PROD_SIZE)).toBe(false);
  });
});
