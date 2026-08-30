/**
 * Exporting a brush composite: the base image is FETCHED, not read off the
 * <img>.
 *
 * Reported (with screenshot): "the brushed result has no base image, only the
 * strokes" — and, moments earlier, "the main image vanished for a second
 * until I pressed undo".
 *
 * Root cause, measured on prod: a request carrying an `Origin` header (which
 * is what `crossOrigin="anonymous"` sends) intermittently gets a 502 from the
 * edge, while the same URL without `Origin` returns 200 every time. The old
 * design loaded the DISPLAY image with `crossOrigin` so the canvas could read
 * it back; one blip → onError → the component flipped to a plain load for
 * good (that's the flash) and every later export was overlay-only.
 *
 * New contract:
 *   - The display <img> never carries `crossOrigin`. Showing a picture needs
 *     no pixel access, so it must not depend on CORS at all.
 *   - `exportComposite` fetches the bytes itself through `apiFetch` (auth +
 *     the dual-channel failover the app already has), draws from a blob URL
 *     (same-origin, so no taint), and retries once on failure.
 *   - When the base cannot be fetched, it says so: `{ blob, baseIncluded:
 *     false }`. The caller decides — and must not save an overlay-only image
 *     as if it were the result.
 */

import { createRef } from 'react';
import { fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn<(path: string, opts?: unknown) => Promise<Response>>();
vi.mock('../../../services/apiClient', () => ({
  apiFetch: (path: string, opts?: unknown) => apiFetch(path, opts),
}));
vi.mock('../smart/mediaUrl', () => ({ mediaSrc: (u: string) => `https://api.test${u}` }));

import { PaintCanvas, type PaintCanvasHandle } from './PaintCanvas';

interface Ctx {
  drawImage: ReturnType<typeof vi.fn>;
  [k: string]: unknown;
}
let ctx: Ctx;

const okResponse = () =>
  new Response(new Blob(['png-bytes'], { type: 'image/png' }), { status: 200 });
const badResponse = () => new Response('bad gateway', { status: 502 });

beforeEach(() => {
  apiFetch.mockReset();
  ctx = {
    drawImage: vi.fn(),
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
    (cb as (b: Blob | null) => void)(new Blob(['x'], { type: 'image/png' }));
  }) as never;
  // jsdom has no createImageBitmap / object URLs; the component decodes the
  // fetched blob through an <img>, which we complete by hand.
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock') as never;
  globalThis.URL.revokeObjectURL = vi.fn() as never;
  Object.defineProperty(HTMLImageElement.prototype, 'src', {
    configurable: true,
    set(this: HTMLImageElement, v: string) {
      this.setAttribute('src', v);
      // Decoding "succeeds" on the next tick.
      setTimeout(() => this.onload?.(new Event('load')), 0);
    },
    get(this: HTMLImageElement) {
      return this.getAttribute('src') ?? '';
    },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderCanvas() {
  const ref = createRef<PaintCanvasHandle>();
  const view = render(
    <PaintCanvas ref={ref} src="/api/v1/generated-media/7/cover" tool="free" color="#f00" size={4} />,
  );
  const img = view.container.querySelector('img') as HTMLImageElement;
  Object.defineProperty(img, 'naturalWidth', { value: 100, configurable: true });
  Object.defineProperty(img, 'naturalHeight', { value: 80, configurable: true });
  fireEvent.load(img);
  return { ref, img, view };
}

describe('PaintCanvas — display never depends on CORS', () => {
  it('renders the base image without a crossOrigin attribute', () => {
    const { img } = renderCanvas();
    expect(
      img.getAttribute('crossorigin'),
      'display image still loaded with crossOrigin — one edge blip and it degrades for good',
    ).toBeNull();
  });
});

describe('PaintCanvas.exportComposite — fetches the base itself', () => {
  it('fetches the base through apiFetch and composites base + annotations', async () => {
    apiFetch.mockResolvedValueOnce(okResponse());
    const { ref } = renderCanvas();

    const out = await ref.current!.exportComposite();

    expect(apiFetch).toHaveBeenCalledWith('/api/v1/generated-media/7/cover', expect.anything());
    expect(out.blob).not.toBeNull();
    expect(out.baseIncluded).toBe(true);
    expect(ctx.drawImage, 'base + overlay').toHaveBeenCalledTimes(2);
  });

  it('retries once when the first fetch fails — the edge is flaky, not broken', async () => {
    apiFetch.mockResolvedValueOnce(badResponse()).mockResolvedValueOnce(okResponse());
    const { ref } = renderCanvas();

    const out = await ref.current!.exportComposite();

    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(out.baseIncluded, 'gave up after a single failed fetch').toBe(true);
  });

  it('reports baseIncluded=false when the base cannot be fetched at all', async () => {
    apiFetch.mockResolvedValue(badResponse());
    const { ref } = renderCanvas();

    const out = await ref.current!.exportComposite();

    expect(out.blob, 'the annotation layer itself is still exportable').not.toBeNull();
    expect(out.baseIncluded, 'silently claimed the base was there').toBe(false);
    expect(ctx.drawImage, 'must not draw the (unreadable) display image').toHaveBeenCalledTimes(1);
  });

  it('treats a thrown fetch the same as a bad status', async () => {
    apiFetch.mockRejectedValue(new Error('network'));
    const { ref } = renderCanvas();

    const out = await ref.current!.exportComposite();

    expect(out.blob).not.toBeNull();
    expect(out.baseIncluded).toBe(false);
  });
});
