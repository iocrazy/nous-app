// features/canvas-core/smart/stitchImages.ts
// IC-parity 宫格拼接 (grid stitch): compose a group's images into one
// √n-grid montage. IC routes this through its image editor's grid-join
// mode; ours renders client-side onto a canvas and returns a PNG blob the
// caller imports back as a generated-media item. Behavior replicated,
// zero code ported (license).

import { fullResSrc } from './mediaUrl';

export const STITCH_CELL = 512;
export const STITCH_MAX = 16;

export function stitchGridFor(count: number): { cols: number; rows: number } {
  const n = Math.max(1, Math.min(count, STITCH_MAX));
  const cols = Math.ceil(Math.sqrt(n));
  return { cols, rows: Math.ceil(n / cols) };
}

async function loadBitmap(url: string): Promise<ImageBitmap> {
  // Full resolution: the montage is imported back as a new generated-media
  // item, so its cells must not be upscaled previews.
  const response = await fetch(fullResSrc(url));
  if (!response.ok) throw new Error(`stitch: fetch failed for ${url}`);
  return createImageBitmap(await response.blob());
}

/** Cover-fit draw: fill the cell, crop the overflow, keep aspect. */
function drawCover(
  ctx: CanvasRenderingContext2D,
  bmp: ImageBitmap,
  x: number,
  y: number,
  size: number,
): void {
  const scale = Math.max(size / bmp.width, size / bmp.height);
  const w = bmp.width * scale;
  const h = bmp.height * scale;
  ctx.drawImage(bmp, x + (size - w) / 2, y + (size - h) / 2, w, h);
}

export interface StitchOptions {
  /** Explicit grid; omitted → √n auto (IC gridJoinAutoDims). */
  rows?: number;
  cols?: number;
  /** Gap between cells in output px (IC 间隔 slider, 0–240). */
  gap?: number;
  /** Long-edge target of the composed image (IC 1K/2K/4K, 256–8192). */
  outputLongEdge?: number;
}

/** Stitch up to STITCH_MAX image urls into one PNG grid blob (IC 宫格拼接:
 *  rows×cols preset, gap, white matting and a long-edge output size). */
export async function stitchImageItems(
  items: Array<{ url: string }>,
  options: StitchOptions = {},
): Promise<Blob> {
  const slice = items.slice(0, STITCH_MAX);
  if (slice.length < 2) throw new Error('stitch needs at least 2 images');
  const auto = stitchGridFor(slice.length);
  const cols = Math.max(1, options.cols ?? auto.cols);
  const rows = Math.max(1, options.rows ?? Math.ceil(slice.length / cols));
  const gap = Math.max(0, Math.min(240, options.gap ?? 0));
  const bitmaps = await Promise.all(slice.map((i) => loadBitmap(i.url)));
  const baseW = cols * STITCH_CELL + (cols - 1) * gap;
  const baseH = rows * STITCH_CELL + (rows - 1) * gap;
  const target = Math.max(256, Math.min(8192, options.outputLongEdge ?? Math.max(baseW, baseH)));
  const scale = target / Math.max(baseW, baseH);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(baseW * scale);
  canvas.height = Math.round(baseH * scale);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('stitch: no 2d context');
  // IC fills the sheet white first — gaps and letterboxed cells must not
  // come out transparent (a PNG with holes looks broken over dark UIs).
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const cell = STITCH_CELL * scale;
  const g = gap * scale;
  bitmaps.forEach((bmp, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    drawCover(ctx, bmp, col * (cell + g), row * (cell + g), cell);
  });
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('stitch: toBlob failed'))),
      'image/png',
    );
  });
}
