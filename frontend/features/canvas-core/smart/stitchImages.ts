// features/canvas-core/smart/stitchImages.ts
// IC-parity 宫格拼接 (grid stitch): compose a group's images into one
// √n-grid montage. IC routes this through its image editor's grid-join
// mode; ours renders client-side onto a canvas and returns a PNG blob the
// caller imports back as a generated-media item. Behavior replicated,
// zero code ported (license).

export const STITCH_CELL = 512;
export const STITCH_MAX = 16;

export function stitchGridFor(count: number): { cols: number; rows: number } {
  const n = Math.max(1, Math.min(count, STITCH_MAX));
  const cols = Math.ceil(Math.sqrt(n));
  return { cols, rows: Math.ceil(n / cols) };
}

async function loadBitmap(url: string): Promise<ImageBitmap> {
  const response = await fetch(url);
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

/** Stitch up to STITCH_MAX image urls into one PNG grid blob. */
export async function stitchImageItems(
  items: Array<{ url: string }>,
): Promise<Blob> {
  const slice = items.slice(0, STITCH_MAX);
  if (slice.length < 2) throw new Error('stitch needs at least 2 images');
  const { cols, rows } = stitchGridFor(slice.length);
  const bitmaps = await Promise.all(slice.map((i) => loadBitmap(i.url)));
  const canvas = document.createElement('canvas');
  canvas.width = cols * STITCH_CELL;
  canvas.height = rows * STITCH_CELL;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('stitch: no 2d context');
  bitmaps.forEach((bmp, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    drawCover(ctx, bmp, col * STITCH_CELL, row * STITCH_CELL, STITCH_CELL);
  });
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('stitch: toBlob failed'))),
      'image/png',
    );
  });
}
