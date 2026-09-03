// features/canvas-core/editor/imageBake.ts
// Client-side pixel baking for the unified editor's Brush and Resize modes
// (IC bakes locally too). jsdom has no 2D canvas, so these stay thin and
// untested at the unit level; the shape/scale MATH lives in callers' tests.

import { fullResSrc } from '../smart/mediaUrl';
import type { PaintShape } from './PaintTool';

async function loadBitmap(src: string): Promise<ImageBitmap> {
  // Full resolution: the bake REPLACES the image, and the canvas is sized
  // from these bitmap dimensions.
  const response = await fetch(fullResSrc(src));
  if (!response.ok) throw new Error(`bake: fetch failed for ${src}`);
  return createImageBitmap(await response.blob());
}

function toBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('bake: toBlob failed'))),
      'image/png',
    );
  });
}

/** Bake paint annotations into the image → PNG blob. */
export async function bakeAnnotations(
  src: string,
  shapes: PaintShape[],
): Promise<Blob> {
  const bmp = await loadBitmap(src);
  const canvas = document.createElement('canvas');
  canvas.width = bmp.width;
  canvas.height = bmp.height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('bake: no 2d context');
  ctx.drawImage(bmp, 0, 0);
  const W = bmp.width;
  const H = bmp.height;
  for (const s of shapes) {
    ctx.strokeStyle = s.color;
    ctx.fillStyle = s.color;
    ctx.lineWidth = Math.max(2, s.size);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const [a, b = a] = s.points;
    if (s.tool === 'free') {
      ctx.beginPath();
      s.points.forEach((p, i) =>
        i === 0 ? ctx.moveTo(p.x * W, p.y * H) : ctx.lineTo(p.x * W, p.y * H),
      );
      ctx.stroke();
    } else if (s.tool === 'rect') {
      ctx.strokeRect(
        Math.min(a.x, b.x) * W,
        Math.min(a.y, b.y) * H,
        Math.abs(b.x - a.x) * W,
        Math.abs(b.y - a.y) * H,
      );
    } else if (s.tool === 'ellipse') {
      ctx.beginPath();
      ctx.ellipse(
        ((a.x + b.x) / 2) * W,
        ((a.y + b.y) / 2) * H,
        (Math.abs(b.x - a.x) / 2) * W,
        (Math.abs(b.y - a.y) / 2) * H,
        0,
        0,
        Math.PI * 2,
      );
      ctx.stroke();
    } else if (s.tool === 'label') {
      const r = Math.max(10, W * 0.015);
      ctx.beginPath();
      ctx.arc(a.x * W, a.y * H, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = `bold ${r}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(s.value ?? '', a.x * W, a.y * H);
    } else if (s.tool === 'text') {
      ctx.font = `bold ${Math.max(14, W * 0.02)}px sans-serif`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(s.value ?? '', a.x * W, a.y * H);
    }
  }
  return toBlob(canvas);
}

/** Downscale by ``scale`` (0..1] → PNG blob. */
export async function bakeResize(src: string, scale: number): Promise<Blob> {
  const bmp = await loadBitmap(src);
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(bmp.width * scale));
  canvas.height = Math.max(1, Math.round(bmp.height * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('bake: no 2d context');
  ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return toBlob(canvas);
}
