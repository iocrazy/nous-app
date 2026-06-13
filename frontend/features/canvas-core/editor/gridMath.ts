/**
 * Pure geometry helpers for the grid-split tool (Phase 3 Day 8).
 *
 * A grid is described by its split lines, normalized [0, 1]:
 * `xs` are vertical lines (x positions), `ys` horizontal lines.
 * The constants mirror the backend `grid_split.py` so the editor
 * can't build a grid the derive endpoint would reject.
 *
 * All helpers are immutable — they return new objects and never
 * touch their inputs.
 */

export interface GridLines {
  /** Vertical split-line x positions, sorted ascending, each in (0, 1). */
  xs: number[];
  /** Horizontal split-line y positions, same constraints. */
  ys: number[];
}

export type GridAxis = 'x' | 'y';

/** No split lines — a single tile. */
export const EMPTY_GRID: GridLines = Object.freeze({ xs: [], ys: [] });

/** Mirrors backend MIN_GAP: minimum distance between adjacent lines
 *  and between a line and the image edge. */
export const MIN_GAP = 0.02;

/** Mirrors backend MAX_LINES_PER_AXIS (≤ 36 tiles per request). */
export const MAX_LINES_PER_AXIS = 5;

function axisLines(lines: GridLines, axis: GridAxis): number[] {
  return axis === 'x' ? lines.xs : lines.ys;
}

function withAxis(lines: GridLines, axis: GridAxis, next: number[]): GridLines {
  return axis === 'x' ? { ...lines, xs: next } : { ...lines, ys: next };
}

/** Evenly-spaced lines for an N-rows × M-cols preset. Parts per axis
 *  are capped at MAX_LINES_PER_AXIS + 1. */
export function presetGrid(rows: number, cols: number): GridLines {
  const evenLines = (parts: number): number[] => {
    const clamped = Math.max(1, Math.min(MAX_LINES_PER_AXIS + 1, Math.floor(parts)));
    return Array.from({ length: clamped - 1 }, (_, i) => (i + 1) / clamped);
  };
  return { xs: evenLines(cols), ys: evenLines(rows) };
}

export function gridShape(lines: GridLines): { rows: number; cols: number } {
  return { rows: lines.ys.length + 1, cols: lines.xs.length + 1 };
}

export function tileCount(lines: GridLines): number {
  const { rows, cols } = gridShape(lines);
  return rows * cols;
}

/** True when there is at least one split line — the backend rejects
 *  a derive call with none. */
export function hasSplit(lines: GridLines): boolean {
  return lines.xs.length > 0 || lines.ys.length > 0;
}

/**
 * Add a line at the midpoint of the largest gap on `axis`. Returns the
 * input unchanged when the axis is full or the largest gap is too
 * small to host a new line with MIN_GAP on both sides.
 */
export function addLineAtLargestGap(lines: GridLines, axis: GridAxis): GridLines {
  const current = axisLines(lines, axis);
  if (current.length >= MAX_LINES_PER_AXIS) return lines;

  const bounds = [0, ...current, 1];
  let bestStart = 0;
  let bestSize = -1;
  for (let i = 0; i < bounds.length - 1; i += 1) {
    const size = bounds[i + 1] - bounds[i];
    if (size > bestSize) {
      bestSize = size;
      bestStart = bounds[i];
    }
  }
  if (bestSize < MIN_GAP * 2) return lines;

  const next = [...current, bestStart + bestSize / 2].sort((a, b) => a - b);
  return withAxis(lines, axis, next);
}

/**
 * Move the line at `index` to `position`, clamped between its
 * neighbours (or the image edges) with MIN_GAP slack. Out-of-range
 * indices return the input unchanged.
 */
export function moveLine(
  lines: GridLines,
  axis: GridAxis,
  index: number,
  position: number,
): GridLines {
  const current = axisLines(lines, axis);
  if (index < 0 || index >= current.length) return lines;

  const lower = (index === 0 ? 0 : current[index - 1]) + MIN_GAP;
  const upper = (index === current.length - 1 ? 1 : current[index + 1]) - MIN_GAP;
  const clamped = Math.min(upper, Math.max(lower, position));
  const next = current.map((value, i) => (i === index ? clamped : value));
  return withAxis(lines, axis, next);
}

/** Remove the line at `index`; out-of-range indices are a no-op. */
export function removeLine(
  lines: GridLines,
  axis: GridAxis,
  index: number,
): GridLines {
  const current = axisLines(lines, axis);
  if (index < 0 || index >= current.length) return lines;
  return withAxis(lines, axis, current.filter((_, i) => i !== index));
}
