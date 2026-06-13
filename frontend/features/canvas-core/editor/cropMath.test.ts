import { describe, expect, it } from 'vitest';

import {
  dragHandle,
  scaleRegionAroundCentre,
  snapRegion,
  snapRegionToAspect,
} from './cropMath';
import {
  FULL_REGION,
  MIN_CROP,
  clampRegion,
  pixelsToRegion,
  regionToPixels,
  type CropRegion,
} from './types';

const TOL = 1e-9;

function expectRegion(actual: CropRegion, expected: CropRegion) {
  expect(actual.x).toBeCloseTo(expected.x, 9);
  expect(actual.y).toBeCloseTo(expected.y, 9);
  expect(actual.width).toBeCloseTo(expected.width, 9);
  expect(actual.height).toBeCloseTo(expected.height, 9);
}

// ============================================================
// clampRegion / regionToPixels / pixelsToRegion
// ============================================================

describe('clampRegion', () => {
  it('passes through a valid region', () => {
    const r = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 };
    expectRegion(clampRegion(r), r);
  });

  it('enforces MIN_CROP for width / height', () => {
    const r = clampRegion({ x: 0, y: 0, width: 0.01, height: 0.01 });
    expect(r.width).toBe(MIN_CROP);
    expect(r.height).toBe(MIN_CROP);
  });

  it('clamps x + width <= 1 by shifting x', () => {
    const r = clampRegion({ x: 0.9, y: 0, width: 0.5, height: 0.5 });
    expect(r.x + r.width).toBeLessThanOrEqual(1 + TOL);
  });
});

describe('regionToPixels / pixelsToRegion', () => {
  it('round-trips a clean half-image rect', () => {
    const region = { x: 0.25, y: 0.5, width: 0.5, height: 0.25 };
    const pixels = regionToPixels(region, 1000, 1000);
    expect(pixels).toEqual({ x: 250, y: 500, width: 500, height: 250 });
    const back = pixelsToRegion(pixels, 1000, 1000);
    expectRegion(back, region);
  });

  it('pixelsToRegion on a zero-sized image returns the full region', () => {
    const back = pixelsToRegion({ x: 0, y: 0, width: 100, height: 100 }, 0, 0);
    expectRegion(back, FULL_REGION);
  });
});

// ============================================================
// dragHandle — corners / sides / move
// ============================================================

describe('dragHandle — move', () => {
  it('translates the rect without changing size', () => {
    const start: CropRegion = { x: 0.2, y: 0.2, width: 0.4, height: 0.4 };
    const next = dragHandle(start, 'move', { dx: 0.1, dy: -0.05 });
    expectRegion(next, { x: 0.3, y: 0.15, width: 0.4, height: 0.4 });
  });

  it('move is clamped at the image edges', () => {
    const start: CropRegion = { x: 0.8, y: 0.8, width: 0.2, height: 0.2 };
    const next = dragHandle(start, 'move', { dx: 0.5, dy: 0.5 });
    // x + width must stay ≤ 1.
    expect(next.x + next.width).toBeLessThanOrEqual(1 + TOL);
    expect(next.y + next.height).toBeLessThanOrEqual(1 + TOL);
  });
});

describe('dragHandle — corner handles pin the opposite corner', () => {
  it('SE handle pulled right + down only grows the rect', () => {
    const start: CropRegion = { x: 0.25, y: 0.25, width: 0.5, height: 0.5 };
    const next = dragHandle(start, 'se', { dx: 0.1, dy: 0.1 });
    // NW corner stayed put.
    expect(next.x).toBeCloseTo(0.25, 9);
    expect(next.y).toBeCloseTo(0.25, 9);
    expect(next.width).toBeCloseTo(0.6, 9);
    expect(next.height).toBeCloseTo(0.6, 9);
  });

  it('NW handle pulled inward shrinks the rect, SE corner stays', () => {
    const start: CropRegion = { x: 0.2, y: 0.2, width: 0.6, height: 0.6 };
    const next = dragHandle(start, 'nw', { dx: 0.1, dy: 0.1 });
    expect(next.x + next.width).toBeCloseTo(0.8, 9);
    expect(next.y + next.height).toBeCloseTo(0.8, 9);
    expect(next.width).toBeCloseTo(0.5, 9);
    expect(next.height).toBeCloseTo(0.5, 9);
  });
});

describe('dragHandle — side handles leave the unrelated axis alone', () => {
  it('E handle changes width but not y/height', () => {
    const start: CropRegion = { x: 0.1, y: 0.1, width: 0.4, height: 0.4 };
    const next = dragHandle(start, 'e', { dx: 0.1, dy: 0.3 });
    expect(next.y).toBeCloseTo(0.1, 9);
    expect(next.height).toBeCloseTo(0.4, 9);
    expect(next.width).toBeCloseTo(0.5, 9);
  });

  it('N handle pulled DOWN shrinks the rect from the top', () => {
    const start: CropRegion = { x: 0.1, y: 0.2, width: 0.4, height: 0.4 };
    const next = dragHandle(start, 'n', { dx: 0, dy: 0.1 });
    expect(next.x).toBeCloseTo(0.1, 9);
    expect(next.width).toBeCloseTo(0.4, 9);
    expect(next.y).toBeCloseTo(0.3, 9);
    expect(next.height).toBeCloseTo(0.3, 9);
  });
});

describe('dragHandle — drag past the opposite edge is floored at MIN_CROP', () => {
  it('SE pulled hard NW does NOT invert the rectangle', () => {
    const start: CropRegion = { x: 0.4, y: 0.4, width: 0.2, height: 0.2 };
    const next = dragHandle(start, 'se', { dx: -0.5, dy: -0.5 });
    expect(next.width).toBeGreaterThanOrEqual(MIN_CROP);
    expect(next.height).toBeGreaterThanOrEqual(MIN_CROP);
  });
});

// ============================================================
// snapRegion + scaleRegionAroundCentre
// ============================================================

describe('snapRegion', () => {
  it('rounds each coordinate to the nearest step', () => {
    const r: CropRegion = { x: 0.13, y: 0.27, width: 0.45, height: 0.61 };
    const snapped = snapRegion(r, 0.1);
    expectRegion(snapped, { x: 0.1, y: 0.3, width: 0.5, height: 0.6 });
  });

  it('step <= 0 leaves the region unchanged', () => {
    const r: CropRegion = { x: 0.13, y: 0.27, width: 0.45, height: 0.61 };
    expectRegion(snapRegion(r, 0), r);
    expectRegion(snapRegion(r, -1), r);
  });
});

describe('scaleRegionAroundCentre', () => {
  it('factor=2 doubles size while preserving centre', () => {
    const r: CropRegion = { x: 0.4, y: 0.4, width: 0.2, height: 0.2 };
    const next = scaleRegionAroundCentre(r, 2);
    expect(next.width).toBeCloseTo(0.4, 9);
    expect(next.height).toBeCloseTo(0.4, 9);
    // Centre invariant.
    expect(next.x + next.width / 2).toBeCloseTo(0.5, 9);
    expect(next.y + next.height / 2).toBeCloseTo(0.5, 9);
  });

  it('factor=0.5 halves size + clamps to MIN_CROP if needed', () => {
    const r: CropRegion = { x: 0.4, y: 0.4, width: 0.2, height: 0.2 };
    const next = scaleRegionAroundCentre(r, 0.5);
    expect(next.width).toBeCloseTo(0.1, 9);
    expect(next.height).toBeCloseTo(0.1, 9);
  });

  it('grow past 1 is clamped to the full image', () => {
    const r: CropRegion = { x: 0.4, y: 0.4, width: 0.2, height: 0.2 };
    const next = scaleRegionAroundCentre(r, 100);
    expect(next.width).toBe(1);
    expect(next.height).toBe(1);
    expect(next.x).toBe(0);
    expect(next.y).toBe(0);
  });
});

// ============================================================
// snapRegionToAspect
// ============================================================

describe('snapRegionToAspect', () => {
  it('1:1 from a wide rectangle keeps width, shortens height, recentres', () => {
    const r: CropRegion = { x: 0.1, y: 0.4, width: 0.6, height: 0.2 };
    const next = snapRegionToAspect(r, 1);
    // larger dim is width (0.6); height becomes 0.6 → recentre.
    expect(next.width).toBeCloseTo(0.6, 9);
    expect(next.height).toBeCloseTo(0.6, 9);
    // centre invariant on the X axis (clamped on Y because 0.6 around
    // 0.5 spans [0.2, 0.8] which still fits — centre preserved).
    expect(next.x + next.width / 2).toBeCloseTo(0.4, 9);
    expect(next.y + next.height / 2).toBeCloseTo(0.5, 9);
  });

  it('16:9 from a tall rectangle widens it, keeping the larger dim', () => {
    const r: CropRegion = { x: 0.4, y: 0.1, width: 0.2, height: 0.6 };
    const next = snapRegionToAspect(r, 16 / 9);
    // larger dim is height (0.6); width = 0.6 * (16/9) ≈ 1.066 → overshoots
    // so both shrink uniformly. After uniform shrink, width/height = 16/9.
    expect(next.width / next.height).toBeCloseTo(16 / 9, 6);
    // both dims must fit in [0, 1].
    expect(next.width).toBeLessThanOrEqual(1);
    expect(next.height).toBeLessThanOrEqual(1);
  });

  it('aspect <= 0 returns the input untouched', () => {
    const r: CropRegion = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 };
    expect(snapRegionToAspect(r, 0)).toEqual(r);
    expect(snapRegionToAspect(r, -2)).toEqual(r);
    expect(snapRegionToAspect(r, NaN)).toEqual(r);
  });

  it('1:1 from a small square is preserved (no change)', () => {
    const r: CropRegion = { x: 0.4, y: 0.4, width: 0.2, height: 0.2 };
    const next = snapRegionToAspect(r, 1);
    expect(next.width).toBeCloseTo(0.2, 9);
    expect(next.height).toBeCloseTo(0.2, 9);
    expect(next.x + next.width / 2).toBeCloseTo(0.5, 9);
    expect(next.y + next.height / 2).toBeCloseTo(0.5, 9);
  });

  it('output never violates MIN_CROP', () => {
    // Both dims already at minimum; ratio stretches one too thin
    // → clampRegion bumps it back to MIN_CROP.
    const r: CropRegion = {
      x: 0.475,
      y: 0.475,
      width: MIN_CROP,
      height: MIN_CROP,
    };
    const next = snapRegionToAspect(r, 16 / 9);
    expect(next.width).toBeGreaterThanOrEqual(MIN_CROP);
    expect(next.height).toBeGreaterThanOrEqual(MIN_CROP);
  });
});
