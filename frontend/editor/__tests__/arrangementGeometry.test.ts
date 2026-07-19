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
  MAX_TIMELINE_SEC,
  MIN_CARD_PX,
  MIN_PX_PER_SEC,
  anchorScrollLeft,
  buildTicks,
  cardWidthPx,
  chooseTickUnit,
  clampPxPerSec,
  computeTotalSec,
  conformBeats,
  fitPxPerSec,
  layoutCards,
  layoutExtentPx,
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

  it('clamps a corrupt/huge start_sec to the display ceiling (tab-freeze DoS guard)', () => {
    expect(computeTotalSec([{ start_sec: 2_147_483_647, duration_sec: 60 }])).toBe(
      MAX_TIMELINE_SEC,
    );
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
    expect(snapGranularity('seconds')).toBe(1);
    expect(snapGranularity('minutes')).toBe(5);
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
  it('caps emitted ticks even for an absurd totalSec (belt-and-braces)', () => {
    expect(buildTicks(2_147_483_647, 'seconds').length).toBeLessThanOrEqual(2000);
  });

  it('emits 10s major ticks in seconds mode, apostrophe on minute boundaries', () => {
    const ticks = buildTicks(60, 'seconds');
    expect(ticks[0]).toEqual({ sec: 0, label: '0s', major: true });
    // minor tick at 5s carries no label
    const five = ticks.find((t) => t.sec === 5);
    expect(five).toEqual({ sec: 5, label: '', major: false });
    const ten = ticks.find((t) => t.sec === 10);
    expect(ten).toEqual({ sec: 10, label: '10s', major: true });
    // the 60s boundary reads as the film apostrophe minute
    const minute = ticks.find((t) => t.sec === 60);
    expect(minute).toEqual({ sec: 60, label: "1'", major: true });
  });

  it('emits apostrophe minute labels in minutes mode', () => {
    const ticks = buildTicks(240, 'minutes');
    expect(ticks[0]).toEqual({ sec: 0, label: "0'", major: true });
    const oneMin = ticks.find((t) => t.sec === 60 && t.major);
    expect(oneMin?.label).toBe("1'");
    expect(ticks[ticks.length - 1].sec).toBe(240);
  });
});

describe('anchorScrollLeft', () => {
  it('pins the time under the cursor across a zoom-in', () => {
    // cursor 100px into the viewport, no scroll, 6→12 px/s: the time there is
    // 100/6 s; after doubling zoom it sits at 200px, so scrollLeft must be 100
    // to keep it under the same 100px cursor offset.
    expect(
      anchorScrollLeft({
        prevPxPerSec: 6,
        nextPxPerSec: 12,
        anchorClientX: 200,
        viewportLeft: 100,
        prevScrollLeft: 0,
      }),
    ).toBe(100);
  });

  it('is a no-op when the zoom does not change', () => {
    expect(
      anchorScrollLeft({
        prevPxPerSec: 6,
        nextPxPerSec: 6,
        anchorClientX: 200,
        viewportLeft: 100,
        prevScrollLeft: 60,
      }),
    ).toBe(60);
  });

  it('clamps to zero instead of scrolling negative', () => {
    expect(
      anchorScrollLeft({
        prevPxPerSec: 6,
        nextPxPerSec: 3,
        anchorClientX: 150,
        viewportLeft: 100,
        prevScrollLeft: 0,
      }),
    ).toBe(0);
  });
});

describe('layoutCards (laper sequential flow)', () => {
  it('keeps back-to-back beats on one lane, pushing right when min-width crowds', () => {
    // 0.2 px/s: each 60s beat is 12px of time but MIN_CARD_PX wide → without the
    // push these would zigzag onto lanes; laper flow keeps one row.
    const layout = layoutCards(
      [
        { id: 'a', start_sec: 0, duration_sec: 60 },
        { id: 'b', start_sec: 60, duration_sec: 60 },
        { id: 'c', start_sec: 120, duration_sec: 60 },
      ],
      0.2,
    );
    expect(layout.get('a')).toEqual({ x: 0, lane: 0, width: MIN_CARD_PX });
    expect(layout.get('b')).toEqual({ x: MIN_CARD_PX, lane: 0, width: MIN_CARD_PX });
    expect(layout.get('c')).toEqual({ x: MIN_CARD_PX * 2, lane: 0, width: MIN_CARD_PX });
  });

  it('positions cards at their true time when the zoom leaves room', () => {
    const layout = layoutCards(
      [
        { id: 'a', start_sec: 0, duration_sec: 60 },
        { id: 'b', start_sec: 90, duration_sec: 60 },
      ],
      6,
    );
    expect(layout.get('a')).toEqual({ x: 0, lane: 0, width: 360 });
    expect(layout.get('b')).toEqual({ x: 540, lane: 0, width: 360 });
  });

  it('splits lanes only on TRUE time overlap', () => {
    const layout = layoutCards(
      [
        { id: 'a', start_sec: 0, duration_sec: 120 },
        { id: 'b', start_sec: 60, duration_sec: 60 }, // starts inside a
      ],
      6,
    );
    expect(layout.get('a')?.lane).toBe(0);
    expect(layout.get('b')?.lane).toBe(1);
  });

  it('seats zero-length beats at the same instant adjacent on one lane', () => {
    const layout = layoutCards(
      [
        { id: 'a', start_sec: 0, duration_sec: null },
        { id: 'b', start_sec: 0, duration_sec: null },
      ],
      6,
    );
    expect(layout.get('a')).toEqual({ x: 0, lane: 0, width: MIN_CARD_PX });
    expect(layout.get('b')).toEqual({ x: MIN_CARD_PX, lane: 0, width: MIN_CARD_PX });
  });

  it('sorts safely when ids are numbers at runtime (Snowflake wire shape)', () => {
    // `id` is typed string but the API sends JSON numbers; two beats sharing a
    // start_sec force the tiebreak comparator, which must not assume a string.
    const numericIds = [
      { id: 200002 as unknown as string, start_sec: 0, duration_sec: 30 },
      { id: 200001 as unknown as string, start_sec: 0, duration_sec: 30 },
    ];
    expect(() => layoutCards(numericIds, 6)).not.toThrow();
    const layout = layoutCards(numericIds, 6);
    // Both placed; the lower id sorts first onto lane 0.
    expect(layout.size).toBe(2);
    expect(layout.get(200001 as unknown as string)?.lane).toBe(0);
  });

  it('layoutExtentPx covers pushed overflow past the ruler end', () => {
    const layout = layoutCards(
      [
        { id: 'a', start_sec: 0, duration_sec: 30 },
        { id: 'b', start_sec: 30, duration_sec: 30 },
      ],
      0.2,
    );
    // ruler span 60s×0.2 = 12px, but two pushed min-width cards need 360px
    expect(layoutExtentPx(layout, 60, 0.2)).toBe(MIN_CARD_PX * 2);
  });
});

describe('computeTotalSec with a target length', () => {
  it('takes the target when it exceeds the furthest beat', () => {
    // target 2700s (45\') beats a single short beat → 2700 (already a whole min)
    expect(computeTotalSec([{ start_sec: 0, duration_sec: 60 }], 2700)).toBe(2700);
  });

  it('lets the furthest beat win when it exceeds the target', () => {
    // furthest end 200s beats a 60s target → ceil(200/60)*60 = 240
    expect(computeTotalSec([{ start_sec: 0, duration_sec: 200 }], 60)).toBe(240);
  });

  it('keeps the 60s floor when the target is smaller and there are no beats', () => {
    expect(computeTotalSec([], 45)).toBe(60);
  });

  it('ignores a null / non-positive target (back-compat with the no-arg call)', () => {
    expect(computeTotalSec([{ start_sec: 30, duration_sec: 40 }], null)).toBe(120);
    expect(computeTotalSec([{ start_sec: 30, duration_sec: 40 }], 0)).toBe(120);
  });

  it('clamps a corrupt/huge target to the display ceiling', () => {
    expect(computeTotalSec([], 2_147_483_647)).toBe(MAX_TIMELINE_SEC);
  });
});

describe('conformBeats', () => {
  it('scales arranged starts + durations by newTotal/oldTotal, snapped to grid', () => {
    const out = conformBeats(
      [
        { id: 'a', start_sec: 0, duration_sec: 30 },
        { id: 'b', start_sec: 30, duration_sec: 30 },
      ],
      60,
      120,
      1,
    );
    expect(out).toEqual([
      { id: 'a', start_sec: 0, duration_sec: 60 },
      { id: 'b', start_sec: 60, duration_sec: 60 },
    ]);
  });

  it('snaps to the minutes-mode 5s grid when stretching to a long total', () => {
    // 60s → 6600s (×110): a midpoint beat at 30s/1s → 3300s/110s, both grid-aligned
    const out = conformBeats([{ id: 'm', start_sec: 30, duration_sec: 1 }], 60, 6600, 5);
    expect(out).toEqual([{ id: 'm', start_sec: 3300, duration_sec: 110 }]);
  });

  it('floors a shrunk duration at one grid step (never collapses to zero)', () => {
    // 600s → 60s (÷10): a 3s beat scales to 0.3s → snaps to 0 → floored to grid 1
    const out = conformBeats([{ id: 's', start_sec: 30, duration_sec: 3 }], 600, 60, 1);
    expect(out).toEqual([{ id: 's', start_sec: 3, duration_sec: 1 }]);
  });

  it('keeps a null duration null', () => {
    const out = conformBeats([{ id: 'x', start_sec: 10, duration_sec: null }], 60, 120, 1);
    expect(out).toEqual([{ id: 'x', start_sec: 20, duration_sec: null }]);
  });

  it('leaves unarranged beats out of the result', () => {
    expect(conformBeats([{ id: 'u', start_sec: null, duration_sec: null }], 60, 120, 1)).toEqual(
      [],
    );
  });

  it('is a no-op guard when oldTotal is non-positive', () => {
    expect(conformBeats([{ id: 'a', start_sec: 10, duration_sec: 5 }], 0, 120, 1)).toEqual([]);
  });
});

