// canvas-kit/knifeGeometry.test.ts
// Knife-cut geometry (Infinite parity — canvas.js segmentsIntersect /
// knifeHitsConnection): pure segment/polyline math, DOM-free.

import { describe, expect, it } from 'vitest';

import {
  segmentsIntersect,
  pointSegmentDistance,
  segmentHitsPolyline,
  segmentIntersectsRect,
} from './knifeGeometry';

describe('segmentsIntersect', () => {
  it('detects a plain crossing', () => {
    expect(
      segmentsIntersect({ x: 0, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }, { x: 10, y: 0 }),
    ).toBe(true);
  });
  it('parallel non-touching segments do not intersect', () => {
    expect(
      segmentsIntersect({ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 5 }, { x: 10, y: 5 }),
    ).toBe(false);
  });
  it('collinear overlapping counts as intersecting', () => {
    expect(
      segmentsIntersect({ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 5, y: 0 }, { x: 15, y: 0 }),
    ).toBe(true);
  });
});

describe('pointSegmentDistance', () => {
  it('perpendicular distance to the middle', () => {
    expect(pointSegmentDistance({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(3);
  });
  it('clamps to the nearest endpoint beyond the segment', () => {
    expect(pointSegmentDistance({ x: 14, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(5);
  });
});

describe('segmentHitsPolyline', () => {
  const POLY = [
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 20, y: 10 },
  ];
  it('hits when the knife segment crosses any polyline segment', () => {
    expect(segmentHitsPolyline({ x: 5, y: -5 }, { x: 5, y: 5 }, POLY, 0)).toBe(true);
  });
  it('misses a distant segment', () => {
    expect(segmentHitsPolyline({ x: 0, y: 50 }, { x: 20, y: 50 }, POLY, 0)).toBe(false);
  });
  it('near-miss within the threshold still counts (zoomed-out forgiveness)', () => {
    expect(segmentHitsPolyline({ x: 0, y: 6 }, { x: 10, y: 6 }, POLY, 8)).toBe(true);
  });
});

describe('segmentIntersectsRect', () => {
  const RECT = { x: 10, y: 10, width: 20, height: 20 };
  it('endpoint inside the rect hits', () => {
    expect(segmentIntersectsRect({ x: 15, y: 15 }, { x: 50, y: 50 }, RECT)).toBe(true);
  });
  it('pass-through hits', () => {
    expect(segmentIntersectsRect({ x: 0, y: 20 }, { x: 50, y: 20 }, RECT)).toBe(true);
  });
  it('clear miss', () => {
    expect(segmentIntersectsRect({ x: 0, y: 0 }, { x: 5, y: 5 }, RECT)).toBe(false);
  });
});
