/**
 * Arrangement timeline geometry (M2) — pure math, no DOM.
 *
 * These functions map beat time offsets to pixel positions on the timeline,
 * snap drag results to the ruler grid, size the ruler, and pack overlapping
 * cards into lanes. They own every "where does this land" decision so the
 * ArrangementView component stays a thin render/pointer shell.
 */
import { describe, expect, it } from 'vitest';

import {
  DEFAULT_PX_PER_SEC,
  MAX_PX_PER_SEC,
  MIN_CARD_PX,
  MIN_PX_PER_SEC,
  assignLanes,
  buildTicks,
  cardWidthPx,
  chooseTickUnit,
  clampPxPerSec,
  computeTotalSec,
  fitPxPerSec,
  pxToTime,
  snapGranularity,
  snapSec,
  stepPxPerSec,
  timeToPx,
} from '../beats/arrangementGeometry';

describe('computeTotalSec', () => {
  it('floors at 60s and rounds up to a whole minute', () => {
    expect(computeTotalSec([])).toBe(60);
    expect(computeTotalSec([{ start_sec: null, duration_sec: null }])).toBe(60);
    // 30 + 40 = 70 → ceil to 120 (2 minutes)
    expect(computeTotalSec([{ start_sec: 30, duration_sec: 40 }])).toBe(120);
    // exact minute stays put
    expect(computeTotalSec([{ start_sec: 0, duration_sec: 120 }])).toBe(120);
  });

  it('treats a null duration as zero length', () => {
    expect(computeTotalSec([{ start_sec: 90, duration_sec: null }])).toBe(120);
  });
});

describe('chooseTickUnit', () => {
  it('uses seconds under 3 minutes and minutes at/above', () => {
    expect(chooseTickUnit(60)).toBe('seconds');
    expect(chooseTickUnit(179)).toBe('seconds');
    expect(chooseTickUnit(180)).toBe('minutes');
    expect(chooseTickUnit(600)).toBe('minutes');
  });
});

describe('snapGranularity + snapSec', () => {
  it('snaps to 5s in seconds mode and 30s in minutes mode', () => {
    expect(snapGranularity('seconds')).toBe(5);
    expect(snapGranularity('minutes')).toBe(30);
    expect(snapSec(37, 5)).toBe(35);
    expect(snapSec(38, 5)).toBe(40);
    expect(snapSec(44, 30)).toBe(30);
    expect(snapSec(46, 30)).toBe(60);
  });

  it('never returns a negative offset', () => {
    expect(snapSec(-3, 5)).toBe(0);
    expect(snapSec(-100, 30)).toBe(0);
  });
});

describe('timeToPx / pxToTime', () => {
  it('are inverse scalings of the px-per-second factor', () => {
    expect(timeToPx(60, 6)).toBe(360);
    expect(pxToTime(360, 6)).toBe(60);
    expect(pxToTime(0, 6)).toBe(0);
  });
});

describe('cardWidthPx', () => {
  it('maps duration to px with a minimum floor', () => {
    expect(cardWidthPx(120, 6)).toBe(720);
    expect(cardWidthPx(null, 6)).toBe(MIN_CARD_PX);
    // a very short beat still clears the min-width floor
    expect(cardWidthPx(2, 6)).toBe(MIN_CARD_PX);
  });
});

describe('fitPxPerSec / clampPxPerSec / stepPxPerSec', () => {
  it('fits the whole timeline into the viewport width', () => {
    expect(fitPxPerSec(240, 1200)).toBe(5);
  });

  it('clamps into the legal zoom band', () => {
    expect(clampPxPerSec(1000)).toBe(MAX_PX_PER_SEC);
    expect(clampPxPerSec(0.00001)).toBe(MIN_PX_PER_SEC);
    expect(clampPxPerSec(6)).toBe(6);
  });

  it('steps up and down and clamps at the band edges', () => {
    expect(stepPxPerSec(6, 1)).toBeGreaterThan(6);
    expect(stepPxPerSec(6, -1)).toBeLessThan(6);
    expect(stepPxPerSec(MAX_PX_PER_SEC, 1)).toBe(MAX_PX_PER_SEC);
    expect(stepPxPerSec(MIN_PX_PER_SEC, -1)).toBe(MIN_PX_PER_SEC);
  });

  it('exposes a sane default zoom inside the band', () => {
    expect(clampPxPerSec(DEFAULT_PX_PER_SEC)).toBe(DEFAULT_PX_PER_SEC);
  });
});

describe('buildTicks', () => {
  it('emits 10s major ticks in seconds mode with labels only on majors', () => {
    const ticks = buildTicks(60, 'seconds');
    expect(ticks[0]).toEqual({ sec: 0, label: '0s', major: true });
    // minor tick at 5s carries no label
    const five = ticks.find((t) => t.sec === 5);
    expect(five).toEqual({ sec: 5, label: '', major: false });
    const ten = ticks.find((t) => t.sec === 10);
    expect(ten).toEqual({ sec: 10, label: '10s', major: true });
    expect(ticks[ticks.length - 1].sec).toBe(60);
  });

  it('emits minute-labelled major ticks in minutes mode', () => {
    const ticks = buildTicks(240, 'minutes');
    expect(ticks[0]).toEqual({ sec: 0, label: '0m', major: true });
    const oneMin = ticks.find((t) => t.sec === 60 && t.major);
    expect(oneMin?.label).toBe('1m');
    expect(ticks[ticks.length - 1].sec).toBe(240);
  });
});

describe('assignLanes', () => {
  it('packs non-overlapping beats onto lane 0 and overlaps onto higher lanes', () => {
    const lanes = assignLanes(
      [
        { id: 'a', start_sec: 0, duration_sec: 30 },
        { id: 'b', start_sec: 40, duration_sec: 20 },
        { id: 'c', start_sec: 10, duration_sec: 50 },
      ],
      0,
    );
    expect(lanes.get('a')).toBe(0);
    expect(lanes.get('b')).toBe(0); // starts after a ends (30 <= 40)
    expect(lanes.get('c')).toBe(1); // overlaps a
  });

  it('respects a minimum visual duration so tiny cards do not visually collide', () => {
    // Two zero-length (null-duration) beats at the same instant must not share a lane.
    const lanes = assignLanes(
      [
        { id: 'a', start_sec: 0, duration_sec: null },
        { id: 'b', start_sec: 0, duration_sec: null },
      ],
      10,
    );
    expect(lanes.get('a')).toBe(0);
    expect(lanes.get('b')).toBe(1);
  });
});
