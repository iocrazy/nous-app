/**
 * Justified (Eagle-style) layout as a PURE PRE-PASS, so the view can
 * virtualize: rows are computed from aspect ratios alone — greedy packing at
 * a target height, scaling each row down to fit the container exactly once
 * it would overflow (the overflowing item stays in the row, Flickr-style).
 * The last row never stretches.
 *
 * Because every row's height is exact before anything renders, the
 * virtualizer needs no DOM measurement — 100k items lay out in one O(n)
 * arithmetic pass (~ms), replacing the previous flex-wrap markup that
 * mounted ALL cards and died around 15k.
 */

export interface JustifiedRow {
  /** Index range [start, end) into the items array. */
  start: number;
  end: number;
  /** Exact row height in px; item i renders at width ar[i] * height. */
  height: number;
}

export interface JustifiedOptions {
  targetRowHeight?: number;
  gap?: number;
}

/** Display clamp — mirrors aspectRatioOf's sane range (ResourceGrid). */
export function clampAspectRatio(ar: number): number {
  if (!Number.isFinite(ar) || ar <= 0) return 1;
  return Math.min(3, Math.max(0.4, ar));
}

export function computeJustifiedRows(
  aspectRatios: number[],
  containerWidth: number,
  { targetRowHeight = 170, gap = 8 }: JustifiedOptions = {},
): JustifiedRow[] {
  if (containerWidth <= 0 || aspectRatios.length === 0) return [];

  const rows: JustifiedRow[] = [];
  let start = 0;
  let sumAr = 0;

  for (let i = 0; i < aspectRatios.length; i += 1) {
    const ar = clampAspectRatio(aspectRatios[i]);
    const n = i - start + 1;
    const widthAtTarget = (sumAr + ar) * targetRowHeight + gap * (n - 1);

    if (widthAtTarget >= containerWidth) {
      // Close the row INCLUDING item i, scaled to fill the width exactly.
      const height = (containerWidth - gap * (n - 1)) / (sumAr + ar);
      rows.push({ start, end: i + 1, height });
      start = i + 1;
      sumAr = 0;
    } else {
      sumAr += ar;
    }
  }

  if (start < aspectRatios.length) {
    // Last row under-fills: keep the target height, never stretch.
    rows.push({ start, end: aspectRatios.length, height: targetRowHeight });
  }

  return rows;
}
