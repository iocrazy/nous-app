import { describe, expect, it } from 'vitest';
import { computePageLayout, type MeasuredRow } from '../paginate';

const row = (key: string, kind: MeasuredRow['kind'], top: number, bottom: number): MeasuredRow => ({
  key,
  kind,
  top,
  bottom,
});

describe('computePageLayout', () => {
  it('breaks before the first row that overflows, with filler padding the page', () => {
    // pageHeight 100: rows [0-40],[45-90],[95-140],[145-190] → row3 overflows.
    const { seams, pageCount } = computePageLayout(
      [
        row('a', 'action', 0, 40),
        row('b', 'action', 45, 90),
        row('c', 'action', 95, 140),
        row('d', 'action', 145, 190),
      ],
      100,
    );
    // Break before c: page-1 content spans 0..95 → filler = 100 - 95 = 5.
    expect(seams).toEqual([{ beforeKey: 'c', filler: 5, page: 1 }]);
    expect(pageCount).toBe(2);
  });

  it('never orphans a CHARACTER cue at a page bottom (keep-with-next)', () => {
    // The dialogue (d) overflows; its cue (c) sits right above → the break
    // moves BEFORE the cue, so cue+dialogue start the next page together.
    const { seams } = computePageLayout(
      [
        row('a', 'action', 0, 60),
        row('c', 'character', 65, 85),
        row('d', 'dialogue', 90, 130),
      ],
      100,
    );
    expect(seams).toEqual([{ beforeKey: 'c', filler: 100 - 65, page: 1 }]);
  });

  it('never leaves a SCENE HEADING as the last row of a page', () => {
    // Heading at 70-90 fits; its first action overflows → break moves before
    // the heading (scene:2), so the scene starts whole on page 2.
    const { seams } = computePageLayout(
      [
        row('a', 'action', 0, 60),
        row('scene:2', 'heading', 70, 90),
        row('b', 'action', 95, 150),
      ],
      100,
    );
    expect(seams).toEqual([{ beforeKey: 'scene:2', filler: 100 - 70, page: 1 }]);
  });

  it('keep-with-next cascades (heading + cue) but never above the page start', () => {
    // heading(65-75) + cue(80-90) + overflowing dialogue(95-140): the chain
    // walks dialogue → cue → heading; break lands before the heading.
    const cascade = computePageLayout(
      [
        row('a', 'action', 0, 60),
        row('scene:2', 'heading', 65, 75),
        row('c', 'character', 80, 90),
        row('d', 'dialogue', 95, 140),
      ],
      100,
    );
    expect(cascade.seams[0].beforeKey).toBe('scene:2');

    // Pathological page of ONLY keep-with-next rows: the chain walks up but
    // STOPS at the page start (c1 must stay on page 1 — forward progress beats
    // the keep rule when every row keeps-with-next). Break lands before c2.
    const pathological = computePageLayout(
      [
        row('c1', 'character', 0, 40),
        row('c2', 'character', 45, 90),
        row('c3', 'character', 95, 140),
      ],
      100,
    );
    expect(pathological.seams).toEqual([{ beforeKey: 'c2', filler: 55, page: 1 }]);
  });

  it('numbers consecutive pages; a row taller than a page cannot loop', () => {
    const { seams, pageCount } = computePageLayout(
      [
        row('a', 'action', 0, 50),
        row('b', 'action', 55, 355), // taller than a whole page
        row('c', 'action', 360, 470),
      ],
      100,
    );
    expect(seams.map((s) => s.beforeKey)).toEqual(['b', 'c']);
    expect(seams.map((s) => s.page)).toEqual([1, 2]);
    expect(pageCount).toBe(3);
  });

  it('empty rows → one page, no seams; everything fitting → no seams', () => {
    expect(computePageLayout([], 100)).toEqual({ seams: [], pageCount: 1 });
    expect(
      computePageLayout(
        [row('a', 'action', 0, 30), row('b', 'action', 35, 80)],
        100,
      ).seams,
    ).toEqual([]);
  });
});
