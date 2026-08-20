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
  playableClips,
  setSegmentRef,
  setSegmentResult,
  ensureSegment,
  segmentStarts,
  activeSegmentAt,
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


// ---- M1 MiniMax workbench: per-clip results ------------------------------

describe('per-clip results (M1)', () => {
  const segs = [
    { id: 'a', prompt: 'p1', seconds: 3 },
    { id: 'b', prompt: 'p2', seconds: 5 },
  ];

  it('setSegmentResult stores the clip url on its segment', () => {
    const out = setSegmentResult(segs, 'b', '/api/v1/generated-media/9/stream');
    expect(out[1].result_url).toBe('/api/v1/generated-media/9/stream');
    expect(out[0].result_url).toBeUndefined();
  });

  it('setSegmentRef stores / clears the i2v reference', () => {
    const withRef = setSegmentRef(segs, 'a', '/api/v1/generated-media/1/cover');
    expect(withRef[0].ref_url).toBe('/api/v1/generated-media/1/cover');
    const cleared = setSegmentRef(withRef, 'a', null);
    expect(cleared[0].ref_url).toBeNull();
  });

  it('reorder keeps results travelling with their segment', () => {
    const withRes = setSegmentResult(segs, 'a', '/r1');
    const out = reorderSegments(withRes, 0, 1);
    expect(out[1].id).toBe('a');
    expect(out[1].result_url).toBe('/r1');
  });

  it('playableClips lists result urls in order, skipping empties', () => {
    const withRes = setSegmentResult(segs, 'b', '/r2');
    expect(playableClips(withRes)).toEqual([{ id: 'b', url: '/r2' }]);
  });
});

// ── IC smartMinimaxEnsureSegment parity (η2) ────────────────────────────────
describe('ensureSegment / segmentStarts / activeSegmentAt', () => {
  it('clamps trim to [0, seconds] keeping at least 0.1s of clip', () => {
    const seg = ensureSegment({
      id: 's1', prompt: '', seconds: 5, trim_in: -2, trim_out: 99,
    });
    expect(seg.trim_in).toBe(0);
    expect(seg.trim_out).toBe(5);
    const tight = ensureSegment({
      id: 's2', prompt: '', seconds: 5, trim_in: 4.99, trim_out: 5,
    });
    expect(tight.trim_out! - (tight.trim_in ?? 0)).toBeCloseTo(0.1, 3);
  });

  it('migrates legacy ref_url into ref_items', () => {
    const seg = ensureSegment({ id: 's1', prompt: '', seconds: 5, ref_url: '/r.png' });
    expect(seg.ref_items).toEqual([{ url: '/r.png', kind: 'image' }]);
  });

  it('segmentStarts packs clips back to back (IC start = prev end)', () => {
    const starts = segmentStarts([
      { id: 'a', prompt: '', seconds: 5 },
      { id: 'b', prompt: '', seconds: 3 },
      { id: 'c', prompt: '', seconds: 4 },
    ]);
    expect(starts).toEqual([0, 5, 8]);
  });

  it('activeSegmentAt finds the clip under the playhead', () => {
    const segs = [
      { id: 'a', prompt: '', seconds: 5 },
      { id: 'b', prompt: '', seconds: 3 },
    ];
    expect(activeSegmentAt(segs, 0)?.id).toBe('a');
    expect(activeSegmentAt(segs, 5.5)?.id).toBe('b');
    expect(activeSegmentAt(segs, 99)).toBeNull();
  });
});
