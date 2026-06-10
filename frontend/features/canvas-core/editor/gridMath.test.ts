import { describe, expect, it } from 'vitest';

import {
  EMPTY_GRID,
  MAX_LINES_PER_AXIS,
  MIN_GAP,
  addLineAtLargestGap,
  gridShape,
  hasSplit,
  moveLine,
  presetGrid,
  removeLine,
  tileCount,
  type GridLines,
} from './gridMath';

describe('presetGrid', () => {
  it('2x2 yields one centred line per axis', () => {
    const grid = presetGrid(2, 2);
    expect(grid.xs).toEqual([0.5]);
    expect(grid.ys).toEqual([0.5]);
  });

  it('3x3 yields thirds', () => {
    const grid = presetGrid(3, 3);
    expect(grid.xs).toHaveLength(2);
    expect(grid.xs[0]).toBeCloseTo(1 / 3);
    expect(grid.xs[1]).toBeCloseTo(2 / 3);
    expect(grid.ys).toHaveLength(2);
  });

  it('1x2 splits columns only', () => {
    const grid = presetGrid(1, 2);
    expect(grid.xs).toEqual([0.5]);
    expect(grid.ys).toEqual([]);
  });

  it('caps parts per axis at MAX_LINES_PER_AXIS + 1', () => {
    const grid = presetGrid(99, 99);
    expect(grid.xs).toHaveLength(MAX_LINES_PER_AXIS);
    expect(grid.ys).toHaveLength(MAX_LINES_PER_AXIS);
  });
});

describe('gridShape / tileCount / hasSplit', () => {
  it('reports rows x cols from line counts', () => {
    const grid: GridLines = { xs: [0.3, 0.6], ys: [0.5] };
    expect(gridShape(grid)).toEqual({ rows: 2, cols: 3 });
    expect(tileCount(grid)).toBe(6);
  });

  it('empty grid is a single tile and not a split', () => {
    expect(gridShape(EMPTY_GRID)).toEqual({ rows: 1, cols: 1 });
    expect(tileCount(EMPTY_GRID)).toBe(1);
    expect(hasSplit(EMPTY_GRID)).toBe(false);
    expect(hasSplit({ xs: [0.5], ys: [] })).toBe(true);
  });
});

describe('addLineAtLargestGap', () => {
  it('first line on an empty axis lands at the centre', () => {
    const next = addLineAtLargestGap(EMPTY_GRID, 'x');
    expect(next.xs).toEqual([0.5]);
    expect(next.ys).toEqual([]);
  });

  it('inserts at the midpoint of the largest gap, keeping sort order', () => {
    const next = addLineAtLargestGap({ xs: [0.2], ys: [] }, 'x');
    // Largest gap is [0.2, 1] → midpoint 0.6.
    expect(next.xs).toHaveLength(2);
    expect(next.xs[0]).toBe(0.2);
    expect(next.xs[1]).toBeCloseTo(0.6);
  });

  it('does not mutate the input', () => {
    const input: GridLines = { xs: [0.5], ys: [] };
    addLineAtLargestGap(input, 'x');
    expect(input.xs).toEqual([0.5]);
  });

  it('returns input unchanged once the axis is full', () => {
    const full = Array.from(
      { length: MAX_LINES_PER_AXIS },
      (_, i) => (i + 1) / (MAX_LINES_PER_AXIS + 1),
    );
    const next = addLineAtLargestGap({ xs: full, ys: [] }, 'x');
    expect(next.xs).toEqual(full);
  });
});

describe('moveLine', () => {
  it('moves a line to the requested position', () => {
    const next = moveLine({ xs: [0.5], ys: [] }, 'x', 0, 0.3);
    expect(next.xs).toEqual([0.3]);
  });

  it('clamps against neighbours with MIN_GAP', () => {
    const next = moveLine({ xs: [0.3, 0.6], ys: [] }, 'x', 0, 0.99);
    expect(next.xs[0]).toBeCloseTo(0.6 - MIN_GAP);
    expect(next.xs[1]).toBe(0.6);
  });

  it('clamps against the image edges with MIN_GAP', () => {
    const low = moveLine({ xs: [0.5], ys: [] }, 'x', 0, -1);
    expect(low.xs[0]).toBeCloseTo(MIN_GAP);
    const high = moveLine({ xs: [0.5], ys: [] }, 'x', 0, 2);
    expect(high.xs[0]).toBeCloseTo(1 - MIN_GAP);
  });

  it('ignores an out-of-range index', () => {
    const grid: GridLines = { xs: [0.5], ys: [] };
    expect(moveLine(grid, 'x', 3, 0.2)).toEqual(grid);
  });

  it('moves horizontal lines on the y axis', () => {
    const next = moveLine({ xs: [], ys: [0.4] }, 'y', 0, 0.7);
    expect(next.ys).toEqual([0.7]);
  });
});

describe('removeLine', () => {
  it('removes the line at the index', () => {
    const next = removeLine({ xs: [0.3, 0.6], ys: [0.5] }, 'x', 1);
    expect(next.xs).toEqual([0.3]);
    expect(next.ys).toEqual([0.5]);
  });

  it('ignores an out-of-range index', () => {
    const grid: GridLines = { xs: [0.5], ys: [] };
    expect(removeLine(grid, 'x', 5)).toEqual(grid);
  });
});
