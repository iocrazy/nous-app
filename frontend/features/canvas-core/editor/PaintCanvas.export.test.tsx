/**
 * Exporting a brush composite when the base image is not CORS-readable.
 *
 * Reported: "I drew something and then Apply Brush does nothing."
 *
 * The base image is loaded `crossOrigin="anonymous"` first; a host without
 * CORS headers rejects that, so the component falls back to a plain load. A
 * plain-loaded cross-origin image TAINTS the canvas it is drawn onto, and
 * `toBlob` on a tainted canvas throws — the export resolves to null and the
 * Apply handler silently does nothing. Nothing is logged where the user can
 * see it; the button just feels dead.
 *
 * The existing code meant to "degrade to overlay-only", but guarded the wrong
 * call: `drawImage` does NOT throw on a cross-origin image — it succeeds and
 * quietly taints. So that fallback never ran.
 *
 * Contract: when the base could not be loaded CORS-clean, export the
 * annotation layer alone. An overlay is worth strictly more than nothing.
 */

import { createRef } from 'react';
import { fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PaintCanvas, type PaintCanvasHandle } from './PaintCanvas';

interface Ctx {
  drawImage: ReturnType<typeof vi.fn>;
  clearRect: ReturnType<typeof vi.fn>;
  getImageData: ReturnType<typeof vi.fn>;
  putImageData: ReturnType<typeof vi.fn>;
  beginPath: ReturnType<typeof vi.fn>;
  [k: string]: unknown;
}

let ctx: Ctx;
/** True once the origin refused a CORS-clean load, so the base image would
 *  taint anything it is drawn onto — exactly how a browser behaves. */
let corsBlocked = false;
/** Set by drawing the blocked base image. Not a knob: tainting is a
 *  CONSEQUENCE of compositing that image, which is the causality the fix
 *  depends on. */
let tainted = false;

beforeEach(() => {
  corsBlocked = false;
  tainted = false;
  ctx = {
    drawImage: vi.fn((source: unknown) => {
      if (corsBlocked && source instanceof HTMLImageElement) tainted = true;
    }),
    clearRect: vi.fn(),
    getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(4) })),
    putImageData: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fill: vi.fn(),
    arc: vi.fn(),
    rect: vi.fn(),
    ellipse: vi.fn(),
    fillText: vi.fn(),
    closePath: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    setLineDash: vi.fn(),
  };
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ctx) as never;
  HTMLCanvasElement.prototype.toBlob = vi.fn(function (this: HTMLCanvasElement, cb) {
    // A tainted canvas throws SecurityError on toBlob — that is the whole bug.
    if (tainted) throw new Error('SecurityError: tainted canvas');
    (cb as (b: Blob | null) => void)(new Blob(['x'], { type: 'image/png' }));
  }) as never;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderCanvas() {
  const ref = createRef<PaintCanvasHandle>();
  const view = render(
    <PaintCanvas ref={ref} src="https://api.example.com/img.png" tool="free" color="#f00" size={4} />,
  );
  const img = view.container.querySelector('img') as HTMLImageElement;
  // Give the overlay a size, as a loaded image would.
  Object.defineProperty(img, 'naturalWidth', { value: 100, configurable: true });
  Object.defineProperty(img, 'naturalHeight', { value: 80, configurable: true });
  return { ref, img, view };
}

describe('PaintCanvas.exportComposite — cross-origin fallback', () => {
  it('includes the base image when it loaded CORS-clean', async () => {
    const { ref, img } = renderCanvas();
    fireEvent.load(img);

    const blob = await ref.current!.exportComposite();
    expect(blob, 'export produced nothing on the happy path').not.toBeNull();
    // base + annotation layer
    expect(ctx.drawImage).toHaveBeenCalledTimes(2);
  });

  it('still produces a blob when the base could not be loaded CORS-clean', async () => {
    const { ref, img } = renderCanvas();
    // The anonymous attempt is rejected by a host without CORS headers, so
    // the component falls back to a plain load. From here, compositing that
    // image taints the canvas.
    corsBlocked = true;
    fireEvent.error(img);
    fireEvent.load(img);

    const blob = await ref.current!.exportComposite();
    expect(
      blob,
      'export returned null — Apply Brush silently does nothing for the user',
    ).not.toBeNull();
  });

  it('exports the annotation layer ALONE in that case, never the tainting base', async () => {
    const { ref, img } = renderCanvas();
    corsBlocked = true;
    fireEvent.error(img);
    fireEvent.load(img);

    await ref.current!.exportComposite();
    expect(
      ctx.drawImage,
      'the base image was still drawn — that is what taints the canvas',
    ).toHaveBeenCalledTimes(1);
  });
});
