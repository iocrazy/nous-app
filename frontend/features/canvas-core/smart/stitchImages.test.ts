import { describe, expect, it } from 'vitest';

import { STITCH_MAX, stitchGridFor } from './stitchImages';

describe('stitchGridFor', () => {
  it.each([
    [2, 2, 1],
    [3, 2, 2],
    [4, 2, 2],
    [5, 3, 2],
    [9, 3, 3],
    [10, 4, 3],
  ])('%i images → %i cols × %i rows', (n, cols, rows) => {
    expect(stitchGridFor(n)).toEqual({ cols, rows });
  });

  it('caps at STITCH_MAX', () => {
    expect(stitchGridFor(99)).toEqual(stitchGridFor(STITCH_MAX));
  });
});
