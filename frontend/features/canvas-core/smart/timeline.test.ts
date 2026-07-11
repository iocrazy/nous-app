// features/canvas-core/smart/timeline.test.ts
// Timeline director node (G8-F1): segment CRUD math over TimelineNodeData —
// pure helpers the view binds to.

import { describe, expect, it } from 'vitest';

import {
  addSegment,
  removeSegment,
  updateSegment,
  totalSeconds,
  DEFAULT_SEGMENT_SECONDS,
  MAX_TIMELINE_SEGMENTS,
} from './timeline';

const SEGS = [
  { id: 's1', prompt: 'opening', seconds: 5 },
  { id: 's2', prompt: 'waves', seconds: 3 },
];

describe('timeline segment helpers', () => {
  it('addSegment appends with defaults and unique id', () => {
    const next = addSegment(SEGS);
    expect(next).toHaveLength(3);
    expect(next[2].seconds).toBe(DEFAULT_SEGMENT_SECONDS);
    expect(new Set(next.map((s) => s.id)).size).toBe(3);
    expect(SEGS).toHaveLength(2); // immutable
  });

  it('addSegment refuses past the cap', () => {
    const full = Array.from({ length: MAX_TIMELINE_SEGMENTS }, (_, i) => ({
      id: `s${i}`, prompt: 'x', seconds: 3,
    }));
    expect(addSegment(full)).toHaveLength(MAX_TIMELINE_SEGMENTS);
  });

  it('removeSegment keeps at least one', () => {
    expect(removeSegment(SEGS, 's2').map((s) => s.id)).toEqual(['s1']);
    expect(removeSegment([SEGS[0]], 's1')).toHaveLength(1);
  });

  it('updateSegment patches one segment immutably and clamps seconds 1-10', () => {
    const next = updateSegment(SEGS, 's2', { seconds: 99 });
    expect(next[1].seconds).toBe(10);
    expect(updateSegment(SEGS, 's2', { seconds: 0 })[1].seconds).toBe(1);
    expect(SEGS[1].seconds).toBe(3);
  });

  it('totalSeconds sums', () => {
    expect(totalSeconds(SEGS)).toBe(8);
  });
});

// ── Minimal-set upgrades (P2-1) ─────────────────────────────────────────────

import { dragSeconds, reorderSegments, reorderThumbs, segmentIndexAtX } from './timeline';

const P21_SEGS = [
  { id: 'a', prompt: 'one', seconds: 5 },
  { id: 'b', prompt: 'two', seconds: 3 },
  { id: 'c', prompt: 'three', seconds: 2 },
];

describe('reorderSegments (P2-1)', () => {
  it('moves a segment forward and backward', () => {
    expect(reorderSegments(P21_SEGS, 0, 2).map((s) => s.id)).toEqual(['b', 'c', 'a']);
    expect(reorderSegments(P21_SEGS, 2, 0).map((s) => s.id)).toEqual(['c', 'a', 'b']);
  });

  it('clamps out-of-range targets and no-ops same-index moves', () => {
    expect(reorderSegments(P21_SEGS, 0, 99).map((s) => s.id)).toEqual(['b', 'c', 'a']);
    expect(reorderSegments(P21_SEGS, 1, 1)).toBe(P21_SEGS);
    expect(reorderSegments(P21_SEGS, -1, 0)).toBe(P21_SEGS);
  });
});

describe('dragSeconds (P2-1)', () => {
  it('converts drag distance to whole seconds at the given scale', () => {
    // 40px per second: +85px ≈ +2s.
    expect(dragSeconds(5, 85, 40)).toBe(7);
    expect(dragSeconds(5, -85, 40)).toBe(3);
  });

  it('clamps to the 1..10 envelope and survives degenerate scales', () => {
    expect(dragSeconds(9, 400, 40)).toBe(10);
    expect(dragSeconds(2, -400, 40)).toBe(1);
    expect(dragSeconds(5, 100, 0)).toBe(5);
  });
});

describe('segmentIndexAtX (P2-1)', () => {
  it('maps a strip x-coordinate to the segment under it (width ∝ seconds)', () => {
    // Total 10s over 200px: a=[0,100), b=[100,160), c=[160,200).
    expect(segmentIndexAtX(P21_SEGS, 50, 200)).toBe(0);
    expect(segmentIndexAtX(P21_SEGS, 130, 200)).toBe(1);
    expect(segmentIndexAtX(P21_SEGS, 190, 200)).toBe(2);
  });

  it('clamps outside the strip', () => {
    expect(segmentIndexAtX(P21_SEGS, -10, 200)).toBe(0);
    expect(segmentIndexAtX(P21_SEGS, 500, 200)).toBe(2);
    expect(segmentIndexAtX([], 50, 200)).toBe(0);
  });
});

describe('reorderThumbs (P2-1)', () => {
  it('thumbnails travel with their segment, padding the sparse tail', () => {
    // 3 segments, tails exist for 0 and 1 only.
    expect(reorderThumbs(['t0', 't1'], 3, 0, 2)).toEqual(['t1', null, 't0']);
    expect(reorderThumbs(['t0', 't1'], 3, 2, 0)).toEqual([null, 't0', 't1']);
  });
});
