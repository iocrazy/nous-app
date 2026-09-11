/**
 * Rasterize a stroke model into the mask PNG the backend expects
 * (Phase 3 Day 11): black background, brush strokes white (keep),
 * eraser strokes re-painted black (un-keep), round caps/joins —
 * matching the SVG preview's geometry.
 *
 * Lives separate from the React components so the canvas dependency
 * is confined to the commit path (jsdom has no real 2D context).
 */

import { strokePath, type MaskStroke } from './maskMath';

export class MaskExportError extends Error {}

/**
 * Draw `strokes` at (width × height) pixels and return the PNG as
 * raw base64 (no data-URL prefix) — the shape the mask commit path
 * decodes back into a PNG file and uploads as the canvas's mask item
 * (`role='mask'`), which then feeds the next generation.
 *
 * Throws MaskExportError when a 2D context is unavailable or the
 * dimensions are degenerate.
 */
export function strokesToMaskPngBase64(
  strokes: MaskStroke[],
  width: number,
  height: number,
): string {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width < 1 || height < 1) {
    throw new MaskExportError(`invalid mask dimensions: ${width}x${height}`);
  }
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(width);
  canvas.height = Math.round(height);
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    throw new MaskExportError('2D canvas context unavailable');
  }

  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  for (const stroke of strokes) {
    if (stroke.points.length === 0) continue;
    ctx.strokeStyle = stroke.tool === 'brush' ? '#ffffff' : '#000000';
    ctx.lineWidth = Math.max(1, stroke.size * canvas.width);
    const path = new Path2D(
      strokePath(stroke.points, canvas.width, canvas.height),
    );
    ctx.stroke(path);
  }

  const dataUrl = canvas.toDataURL('image/png');
  const comma = dataUrl.indexOf(',');
  if (comma < 0) {
    throw new MaskExportError('canvas toDataURL returned no payload');
  }
  return dataUrl.slice(comma + 1);
}
