/**
 * computeJustifiedRows — the pure layout pass behind the virtualized
 * justified (Eagle-style) view. Greedy row packing: items join a row at the
 * target height until the row would overflow the container, then the whole
 * row (including the overflowing item) scales down to fit exactly. The last
 * row never stretches. Row heights are exact, so the virtualizer needs no
 * DOM measurement.
 */

import { describe, expect, it } from 'vitest';
import { computeJustifiedRows, distributeRowWidths, type JustifiedRow } from './justifiedLayout';

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

describe('computeJustifiedRows — incremental reuse', () => {
  /** Deterministic pseudo-random ratios, so a failure is reproducible. */
  function ratios(n: number, seed: number): number[] {
    let s = seed;
    return Array.from({ length: n }, () => {
      s = (s * 1103515245 + 12345) % 2147483648;
      return 0.4 + (s / 2147483648) * 2.6;
    });
  }

  it('produces EXACTLY the full-recompute result for every change position', () => {
    // The reuse hint is a cost optimisation, so its only correctness duty is to
    // be indistinguishable from recomputing from scratch. Sweep the change
    // position across the whole list rather than spot-checking one index.
    const base = ratios(120, 7);
    const prevRows = computeJustifiedRows(base, WIDTH, { targetRowHeight: TARGET, gap: GAP });

    for (let changed = 0; changed < base.length; changed += 1) {
      const next = [...base];
      next[changed] = next[changed] > 1.5 ? 0.5 : 2.7;

      const full = computeJustifiedRows(next, WIDTH, { targetRowHeight: TARGET, gap: GAP });
      const incremental = computeJustifiedRows(
        next,
        WIDTH,
        { targetRowHeight: TARGET, gap: GAP },
        { prevRows, firstChangedIndex: changed },
      );

      expect(incremental).toEqual(full);
    }
  });

  it('keeps the untouched prefix rows byte-identical (same object identity)', () => {
    const base = ratios(60, 11);
    const prevRows = computeJustifiedRows(base, WIDTH, { targetRowHeight: TARGET, gap: GAP });

    // Change an item late in the list so several rows precede it.
    const changed = prevRows[3].start;
    const next = [...base];
    next[changed] = 0.45;

    const rows = computeJustifiedRows(
      next,
      WIDTH,
      { targetRowHeight: TARGET, gap: GAP },
      { prevRows, firstChangedIndex: changed },
    );

    // Rows entirely before the changed item are REUSED, not rebuilt.
    for (let i = 0; i < 3; i += 1) expect(rows[i]).toBe(prevRows[i]);
    // The row containing the change is not reused.
    expect(rows[3]).not.toBe(prevRows[3]);
  });

  it('recomputes everything when the change is at index 0', () => {
    const base = ratios(40, 3);
    const prevRows = computeJustifiedRows(base, WIDTH, { targetRowHeight: TARGET, gap: GAP });
    const next = [...base];
    next[0] = 2.9;

    const rows = computeJustifiedRows(
      next,
      WIDTH,
      { targetRowHeight: TARGET, gap: GAP },
      { prevRows, firstChangedIndex: 0 },
    );
    expect(rows).toEqual(
      computeJustifiedRows(next, WIDTH, { targetRowHeight: TARGET, gap: GAP }),
    );
  });

  it('ignores an empty prevRows hint', () => {
    const ars = ratios(25, 5);
    expect(
      computeJustifiedRows(ars, WIDTH, { targetRowHeight: TARGET, gap: GAP }, {
        prevRows: [],
        firstChangedIndex: 10,
      }),
    ).toEqual(computeJustifiedRows(ars, WIDTH, { targetRowHeight: TARGET, gap: GAP }));
  });
})

describe('distributeRowWidths — 竖版卡片不能窄到数据看不见', () => {
  // 用户报告（2026-09-15，我的下载 · 来源 Douyin）：竖版视频卡片比图片窄一大截，
  // 卡片下面那条四格统计（点赞/评论/分享/收藏）被挤成一团，数字看不清。
  // 9:16 = 0.5625，4:3 = 1.333 —— 同一行等高，宽度差 2.4 倍。
  const P = 0.5625; // 竖版视频
  const L = 1.3333; // 横版图片
  const H = 200;    // 目标行高
  const TIGHT = 167; // 缩放后的行高 —— 这一行付不起下限
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);

  it('把不足下限的卡片抬到下限', () => {
    const w = distributeRowWidths([P, L, L], H, { minWidth: 190 });
    expect(w[0]).toBe(190);
  });

  it('整行总宽一个像素都不变 —— 抬宽是行内再分配，不是把行撑破', () => {
    const w = distributeRowWidths([P, L, L], H, { minWidth: 190 });
    expect(sum(w)).toBeCloseTo(sum([P, L, L].map((a) => a * H)), 5);
  });

  it('多出来的宽度是从宽卡片身上匀的，而且按比例', () => {
    const w = distributeRowWidths([P, L, L], H, { minWidth: 190 });
    // 两张图片本来一样宽，匀完之后还是一样宽（等比缩，不是谁吃亏）。
    expect(w[1]).toBeCloseTo(w[2], 5);
    expect(w[1]).toBeLessThan(L * H);
  });

  it('最后一行不会被撑满 —— 它本来就该是短的', () => {
    // computeJustifiedRows 的最后一行保持目标高度、不拉伸。用容器宽度当预算
    // 会把这一行的图片硬撑到整行宽，是这个函数第一版写错的地方。
    const w = distributeRowWidths([P, L], H, { minWidth: 190 });
    expect(sum(w)).toBeCloseTo(P * H + L * H, 5);
    expect(w[1]).toBeLessThan(L * H);
  });

  it('没有卡片低于下限时，一个像素都不动', () => {
    const w = distributeRowWidths([L, L], H, { minWidth: 190 });
    expect(w).toEqual([L * H, L * H]);
  });

  it('不传下限就是原来的行为', () => {
    const w = distributeRowWidths([P, L], H, {});
    expect(w).toEqual([P * H, L * H]);
  });

  it('整行付不起下限时平分同样的总宽 —— 那才是最宽的「最窄卡片」', () => {
    // [竖,横,横] 行高 167：总宽 539px，三张都要 190 需要 570px，付不起。
    // 平分得到 179.75 —— 比强行把竖版抬到 190、把两张图压到 174 更好，
    // 因为它最大化了这一行里最窄的那张。
    const w = distributeRowWidths([P, L, L], TIGHT, { minWidth: 190 });
    expect(sum(w)).toBeCloseTo((P + L + L) * TIGHT, 5);
    expect(new Set(w.map((x) => x.toFixed(4))).size).toBe(1);
    // 仍然清楚地高于数字挤不下的那条线（(W-34)/4 >= 35 → W >= 174）。
    expect(Math.min(...w)).toBeGreaterThan(174);
  });

  it('整行全是竖版时也平分，总宽原样保住', () => {
    const w = distributeRowWidths([P, P, P], H, { minWidth: 190 });
    expect(sum(w)).toBeCloseTo(P * H * 3, 5);
    expect(new Set(w.map((x) => x.toFixed(4))).size).toBe(1);
  });

  it('绝不为了抬窄卡片而把宽卡片压到下限以下', () => {
    // 抬一个就得压另一个到 190 以下时，宁可平分 —— 否则只是把「谁看不清」
    // 换了个人，而这正是这个函数要消掉的毛病。
    const w = distributeRowWidths([P, 0.8], H, { minWidth: 190 });
    expect(sum(w)).toBeCloseTo((P + 0.8) * H, 5);
    expect(new Set(w.map((x) => x.toFixed(4))).size).toBe(1);
  });

  it('空行不炸', () => {
    expect(distributeRowWidths([], H, { minWidth: 190 })).toEqual([]);
  });
});

describe('distributeRowWidths — 抬宽不能把问题转嫁给别人', () => {
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);

  it('为窄卡片缩宽卡片时，不把中等卡片压到下限以下', () => {
    // 实测撞到的那组：[16:9, 9:16, 1:1] 行高 200。只钉一次的话，正方形那张
    // 会被等比缩到 172px —— 比它要救的竖版还窄，等于把「看不清」换了个人。
    const w = distributeRowWidths([16 / 9, 9 / 16, 1], 200, { minWidth: 190 });
    for (const x of w) expect(x).toBeGreaterThanOrEqual(190);
    expect(sum(w)).toBeCloseTo((16 / 9 + 9 / 16 + 1) * 200, 5);
  });

  it('连锁钉住会收敛，不会打转', () => {
    // 一张很宽 + 一串刚好在边界上的：每一轮都会再钉住一个，必须停下来。
    const w = distributeRowWidths([3, 0.95, 0.96, 0.97, 0.5625], 200, { minWidth: 190 });
    expect(w).toHaveLength(5);
    expect(w.every((x) => Number.isFinite(x) && x > 0)).toBe(true);
    expect(sum(w)).toBeCloseTo((3 + 0.95 + 0.96 + 0.97 + 0.5625) * 200, 5);
  });
});
