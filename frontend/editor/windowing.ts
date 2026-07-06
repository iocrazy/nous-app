/**
 * Windowed scene rendering (spec performance budget, Task 10).
 *
 * Long scripts (100+ scenes, each a live contentEditable subtree) are expensive
 * to keep fully mounted. Above WINDOW_THRESHOLD scenes the shell mounts only the
 * scenes near the viewport (± WINDOW_OVERSCAN) and replaces the rest with fixed-
 * height placeholders whose heights are ESTIMATED from element counts, so the
 * scrollbar geometry stays roughly correct without measuring every block.
 *
 * `computeSceneWindow` is a pure function of the estimated heights + scroll
 * position, which makes the "≤ 2·overscan+1 mounted" invariant unit-testable
 * with no real layout (jsdom reports zero sizes).
 */

/** Above this many scenes, switch on windowing. */
export const WINDOW_THRESHOLD = 30;

/** Scenes mounted on each side of the visible range. */
export const WINDOW_OVERSCAN = 5;

/** Fixed estimate for a scene's head row + margins (px). */
const SCENE_HEADER_PX = 120;

/** Fixed estimate for a single element line (px). */
const ELEMENT_LINE_PX = 30;

/** Estimated rendered height of a scene from its element count. */
export function estimateSceneHeight(elementCount: number): number {
  return SCENE_HEADER_PX + Math.max(1, elementCount) * ELEMENT_LINE_PX;
}

export interface SceneWindow {
  /** First scene index to mount (inclusive). */
  start: number;
  /** Last scene index to mount (inclusive); -1 when there are no scenes. */
  end: number;
}

/**
 * The inclusive index range to mount for a given scroll offset. Finds the first
 * scene whose estimated bottom crosses `scrollTop` (viewport top) and the first
 * whose bottom crosses the viewport bottom, then pads both ends by `overscan`.
 */
export function computeSceneWindow(
  heights: number[],
  scrollTop: number,
  viewportHeight: number,
  overscan: number = WINDOW_OVERSCAN,
): SceneWindow {
  const n = heights.length;
  if (n === 0) return { start: 0, end: -1 };

  const bottoms: number[] = new Array(n);
  let acc = 0;
  for (let i = 0; i < n; i += 1) {
    acc += heights[i];
    bottoms[i] = acc;
  }

  const firstAtOrAfter = (offset: number): number => {
    const idx = bottoms.findIndex((b) => b > offset);
    return idx === -1 ? n - 1 : idx;
  };

  const topIdx = firstAtOrAfter(scrollTop);
  const bottomIdx = firstAtOrAfter(scrollTop + viewportHeight);

  return {
    start: Math.max(0, topIdx - overscan),
    end: Math.min(n - 1, bottomIdx + overscan),
  };
}
