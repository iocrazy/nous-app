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
