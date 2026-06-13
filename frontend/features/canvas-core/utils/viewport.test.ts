import { describe, expect, it } from 'vitest';

import {
  IDENTITY_VIEWPORT,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  panByScreenDelta,
  screenToWorld,
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
