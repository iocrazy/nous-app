import { describe, expect, it } from 'vitest';
import { computePageBreaks } from '../paginate';

describe('computePageBreaks', () => {
  it('emits a break before the first row that overflows the page window', () => {
    // pageHeight 100; rows: [0-40],[45-90],[95-140],[145-190]
    // row3 (95-140) bottom exceeds 100 → break before it; next window from 95;
    // row4 bottom 190-95=95 fits.
    const breaks = computePageBreaks(
      [
        { top: 0, bottom: 40 },
        { top: 45, bottom: 90 },
        { top: 95, bottom: 140 },
        { top: 145, bottom: 190 },
      ],
      100,
    );
    expect(breaks).toEqual([{ y: 95 - 9, page: 1 }]);
  });

  it('numbers consecutive pages and never loops on a row taller than a page', () => {
    // A 300-tall row inside 100-tall pages: one break before it, one after the
    // next overflowing row — page numbers increment monotonically.
    const breaks = computePageBreaks(
      [
        { top: 0, bottom: 50 },
        { top: 55, bottom: 355 }, // taller than a whole page
        { top: 360, bottom: 470 }, // also overflows its window
      ],
      100,
    );
    expect(breaks).toEqual([
      { y: 55 - 9, page: 1 },
      { y: 360 - 9, page: 2 },
    ]);
  });

  it('no rows → no breaks; everything fitting one page → no breaks', () => {
    expect(computePageBreaks([], 100)).toEqual([]);
    expect(
      computePageBreaks(
        [
          { top: 0, bottom: 30 },
          { top: 35, bottom: 80 },
        ],
        100,
      ),
    ).toEqual([]);
  });
});
