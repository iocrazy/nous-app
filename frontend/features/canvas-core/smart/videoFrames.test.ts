// features/canvas-core/smart/videoFrames.test.ts
// Frame-step math for the video lightbox (P2-8 — Infinite's
// videoFrameStep / seekPreviewFrames): fixed 30fps stepping clamped inside
// the clip, plus the export-frame target picker (first / current / last).

import { describe, expect, it } from 'vitest';

import {
  FRAME_STEP_SECONDS,
  exportFrameTime,
  nextFrameTime,
} from './videoFrames';

describe('nextFrameTime', () => {
  it('steps forward and backward by one 30fps frame', () => {
    expect(nextFrameTime(1, +1, 10)).toBeCloseTo(1 + FRAME_STEP_SECONDS, 6);
    expect(nextFrameTime(1, -1, 10)).toBeCloseTo(1 - FRAME_STEP_SECONDS, 6);
  });

  it('clamps at zero and just short of the duration (Infinite: duration - step/2)', () => {
    expect(nextFrameTime(0, -1, 10)).toBe(0);
    const nearEnd = nextFrameTime(10, +1, 10);
    expect(nearEnd).toBeLessThan(10);
    expect(nearEnd).toBeCloseTo(10 - FRAME_STEP_SECONDS / 2, 6);
  });

  it('degenerate durations do not throw', () => {
    expect(nextFrameTime(0, +1, 0)).toBe(0);
    expect(nextFrameTime(5, +1, Number.NaN)).toBe(5);
  });
});

describe('exportFrameTime', () => {
  it('first = 0, last = duration - step/2, current = the given time', () => {
    expect(exportFrameTime('first', 4, 10)).toBe(0);
    expect(exportFrameTime('last', 4, 10)).toBeCloseTo(10 - FRAME_STEP_SECONDS / 2, 6);
    expect(exportFrameTime('current', 4, 10)).toBe(4);
  });

  it('clamps a current time that sits past the safe last frame', () => {
    expect(exportFrameTime('current', 999, 10)).toBeCloseTo(
      10 - FRAME_STEP_SECONDS / 2,
      6,
    );
  });
});
