/**
 * Pure geometry helpers for the crop tool (Phase 3 Day 1).
 *
 * Kept separate from the React component so the math is
 * exhaustively-testable without a DOM.
 */

import { MIN_CROP, clampRegion, type CropHandle, type CropRegion } from './types';

interface Delta {
  /** Drag delta in normalized image units (range typically [-1, 1]). */
  dx: number;
  dy: number;
}

/**
 * Apply a drag delta from `handle` to `start` and return the new region
 * clamped into bounds.
 *
 * `move` translates the rectangle; the eight resize handles each grow
 * or shrink the rectangle from the opposite edge. North/south handles
 * leave the x-axis alone, etc.
 */
export function dragHandle(
  start: CropRegion,
  handle: CropHandle,
  { dx, dy }: Delta,
): CropRegion {
  if (handle === 'move') {
    return clampRegion({ ...start, x: start.x + dx, y: start.y + dy });
  }

  let { x, y, width, height } = start;
  const right = x + width;
  const bottom = y + height;

  // Update the relevant axis. We always pin the OPPOSITE edge in
  // place — pinning the moved edge would let the rectangle fly off.
  if (handle.includes('n')) {
    const newY = clamp01(y + dy);
    height = Math.max(MIN_CROP, bottom - newY);
    y = bottom - height;
  } else if (handle.includes('s')) {
    const newBottom = clamp01(bottom + dy);
    height = Math.max(MIN_CROP, newBottom - y);
  }
  if (handle.includes('w')) {
    const newX = clamp01(x + dx);
    width = Math.max(MIN_CROP, right - newX);
    x = right - width;
  } else if (handle.includes('e')) {
    const newRight = clamp01(right + dx);
    width = Math.max(MIN_CROP, newRight - x);
  }

  return clampRegion({ x, y, width, height });
}

function clamp01(v: number): number {
  if (Number.isNaN(v)) return 0;
  return Math.min(1, Math.max(0, v));
}

/** Snap a region's edges to a fractional grid (e.g. 1/12 thirds, or
 *  a uniform 1/16 step). Returns the input untouched when `step <= 0`
 *  so the caller can disable snapping. */
export function snapRegion(region: CropRegion, step: number): CropRegion {
  if (!Number.isFinite(step) || step <= 0) return region;
  const snap = (v: number) => Math.round(v / step) * step;
  return clampRegion({
    x: snap(region.x),
    y: snap(region.y),
    width: Math.max(MIN_CROP, snap(region.width)),
    height: Math.max(MIN_CROP, snap(region.height)),
  });
}

/**
 * Centre-anchor zoom — used by ctrl/cmd+wheel to grow/shrink the
 * region around its centre while keeping it inside bounds.
 *
 * `factor` < 1 shrinks, > 1 expands. The result is always at least
 * MIN_CROP×MIN_CROP and never exceeds the full image.
 */
export function scaleRegionAroundCentre(
  region: CropRegion,
  factor: number,
): CropRegion {
  if (!Number.isFinite(factor) || factor <= 0) return region;
  const centreX = region.x + region.width / 2;
  const centreY = region.y + region.height / 2;
  const width = Math.min(1, Math.max(MIN_CROP, region.width * factor));
  const height = Math.min(1, Math.max(MIN_CROP, region.height * factor));
  return clampRegion({
    x: centreX - width / 2,
    y: centreY - height / 2,
    width,
    height,
  });
}
