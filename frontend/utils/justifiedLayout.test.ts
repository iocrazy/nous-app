/**
 * computeJustifiedRows — the pure layout pass behind the virtualized
 * justified (Eagle-style) view. Greedy row packing: items join a row at the
 * target height until the row would overflow the container, then the whole
 * row (including the overflowing item) scales down to fit exactly. The last
 * row never stretches. Row heights are exact, so the virtualizer needs no
 * DOM measurement.
 */

import { describe, expect, it } from 'vitest';
import { computeJustifiedRows, type JustifiedRow } from './justifiedLayout';

const WIDTH = 1000;
const TARGET = 170;
const GAP = 8;

function rowWidth(row: JustifiedRow, ars: number[]): number {
  const n = row.end - row.start;
  const itemsW = ars
    .slice(row.start, row.end)
    .reduce((acc, ar) => acc + ar * row.height, 0);
  return itemsW + GAP * (n - 1);
}

describe('computeJustifiedRows', () => {
  it('returns [] for empty input or zero width', () => {
    expect(computeJustifiedRows([], WIDTH)).toEqual([]);
    expect(computeJustifiedRows([1, 1], 0)).toEqual([]);
  });

  it('packs rows greedily and scales each full row to exactly the width', () => {
    // 10 square items: at target height 170 each is 170 wide; 1000px fits
    // 5 per row (5*170 + 4*8 = 882 < 1000; adding a 6th = 1060 > 1000 → the
    // 6-item row compresses to fit exactly).
    const ars = Array(10).fill(1);
    const rows = computeJustifiedRows(ars, WIDTH, { targetRowHeight: TARGET, gap: GAP });
    expect(rows.length).toBeGreaterThan(1);
    // Every non-final row fills the container width exactly (±0.5px float).
    for (const row of rows.slice(0, -1)) {
      expect(Math.abs(rowWidth(row, ars) - WIDTH)).toBeLessThan(0.5);
      // Compressed below target, never stretched above it.
      expect(row.height).toBeLessThanOrEqual(TARGET);
      expect(row.height).toBeGreaterThan(0);
    }
    // Rows partition the items: contiguous, complete, no overlap.
    expect(rows[0].start).toBe(0);
    expect(rows[rows.length - 1].end).toBe(ars.length);
    for (let i = 1; i < rows.length; i += 1) {
      expect(rows[i].start).toBe(rows[i - 1].end);
    }
  });

  it('keeps the last row at target height (no stretch) when it under-fills', () => {
    // 7 squares: the first 6 close a compressed row (5×170+4×8 < 1000 but
    // adding the 6th overflows → Flickr-style the row keeps it and scales);
    // the 1 leftover must stay at target height, never stretched to fill.
    const ars = Array(7).fill(1);
    const rows = computeJustifiedRows(ars, WIDTH, { targetRowHeight: TARGET, gap: GAP });
    expect(rows).toHaveLength(2);
    expect(rows[0].end - rows[0].start).toBe(6);
    const last = rows[rows.length - 1];
    expect(last.end - last.start).toBe(1);
    expect(last.height).toBe(TARGET);
  });

  it('degenerates to a uniform grid when every item is square', () => {
    // The "no dimensions known yet" case for non-visual files: all aspect
    // ratios equal 1. Every full row must then hold the same number of items
    // at the same height, i.e. look exactly like the fixed grid.
    const ars = Array(20).fill(1);
    const rows = computeJustifiedRows(ars, WIDTH, { targetRowHeight: TARGET, gap: GAP });

    const fullRows = rows.slice(0, -1);
    expect(fullRows.length).toBeGreaterThan(1);

    const counts = new Set(fullRows.map((r) => r.end - r.start));
    expect(counts.size).toBe(1);

    for (const row of fullRows) {
      expect(row.height).toBeCloseTo(fullRows[0].height, 6);
      expect(rowWidth(row, ars)).toBeCloseTo(WIDTH, 6);
    }
  });

  it('gives an ultra-wide item its own compressed row', () => {
    // ar=3 clamped max; 3*170=510 fits 1000 so pair with squares; use a
    // narrow container to force solo: 3*170=510 > 400 → solo row scaled down.
    const rows = computeJustifiedRows([3, 1], 400, { targetRowHeight: TARGET, gap: GAP });
    expect(rows[0].end - rows[0].start).toBe(1);
    expect(rows[0].height).toBeCloseTo(400 / 3, 1);
  });

  it('clamps degenerate aspect ratios into the sane display range', () => {
    // 0 / negative / NaN aspect ratios must not produce Infinity heights.
    const rows = computeJustifiedRows([0, -5, Number.NaN, 100], WIDTH, {
      targetRowHeight: TARGET,
      gap: GAP,
    });
    for (const row of rows) {
      expect(Number.isFinite(row.height)).toBe(true);
      expect(row.height).toBeGreaterThan(0);
    }
  });

  it('is O(n)-cheap at 100k items', () => {
    const ars = Array.from({ length: 100_000 }, (_, i) => 0.5 + (i % 20) / 10);
    const t0 = performance.now();
    const rows = computeJustifiedRows(ars, WIDTH);
    const elapsed = performance.now() - t0;
    expect(rows[rows.length - 1].end).toBe(100_000);
    expect(elapsed).toBeLessThan(200); // generous CI headroom; ~ms locally
  });
});
