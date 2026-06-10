/**
 * Image-editor types (Phase 3 Day 1).
 *
 * Coordinates are NORMALIZED — (0,0) is the top-left of the source
 * image, (1,1) is the bottom-right. This makes the crop rectangle
 * resolution-independent: applying it to the original asset, a
 * thumbnail, or a resized preview yields the same logical region.
 */

export interface CropRegion {
  /** Top-left x, range [0, 1]. */
  x: number;
  /** Top-left y, range [0, 1]. */
  y: number;
  /** Width, range (0, 1]. */
  width: number;
  /** Height, range (0, 1]. */
  height: number;
}

/** A no-op crop covering the full image. */
export const FULL_REGION: CropRegion = Object.freeze({
  x: 0,
  y: 0,
  width: 1,
  height: 1,
});

/** Smallest crop a user can produce — 5% of either dimension. Drag
 *  handles snap to this floor so dragging past zero doesn't invert
 *  the rectangle. */
export const MIN_CROP = 0.05;

/**
 * Apply a normalized region to a concrete bitmap size, returning the
 * pixel-space rectangle. Tests use this to assert the geometry without
 * needing a layout/DOM round-trip.
 */
export interface PixelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function regionToPixels(
  region: CropRegion,
  imageWidth: number,
  imageHeight: number,
): PixelRect {
  return {
    x: Math.round(region.x * imageWidth),
    y: Math.round(region.y * imageHeight),
    width: Math.round(region.width * imageWidth),
    height: Math.round(region.height * imageHeight),
  };
}

/**
 * Inverse of regionToPixels. Useful when the caller wires a server
 * response carrying a pixel rect back into the normalized form.
 */
export function pixelsToRegion(
  rect: PixelRect,
  imageWidth: number,
  imageHeight: number,
): CropRegion {
  if (imageWidth <= 0 || imageHeight <= 0) return FULL_REGION;
  return clampRegion({
    x: rect.x / imageWidth,
    y: rect.y / imageHeight,
    width: rect.width / imageWidth,
    height: rect.height / imageHeight,
  });
}

/** Clamp into [0, 1] and enforce MIN_CROP for width/height. */
export function clampRegion(region: CropRegion): CropRegion {
  const minSize = MIN_CROP;
  const width = Math.min(1, Math.max(minSize, region.width));
  const height = Math.min(1, Math.max(minSize, region.height));
  const x = Math.min(1 - width, Math.max(0, region.x));
  const y = Math.min(1 - height, Math.max(0, region.y));
  return { x, y, width, height };
}

/** Which side or corner of the rectangle a drag started from. */
export type CropHandle =
  | 'nw'
  | 'ne'
  | 'sw'
  | 'se'
  | 'n'
  | 's'
  | 'e'
  | 'w'
  | 'move';
