// features/canvas-core/smart/nodes/OutputLightbox.video.test.tsx
// Video lightbox frame stepping + export (P2-8): wheel and arrow keys
// pause-and-seek by 30fps frames instead of zooming/switching; the toolbar
// exports first/current/last frames as PNGs. jsdom has no <video>/<canvas>
// implementation, so we stub the media surface.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const downloadBlob = vi.fn();
vi.mock('../downloadMedia', () => ({
  downloadUrl: vi.fn(),
  downloadName: (item: { name?: string }) => item.name ?? 'x',
  downloadBlob: (...a: unknown[]) => downloadBlob(...a),
}));

import { OutputLightbox } from './OutputLightbox';
import { FRAME_STEP_SECONDS } from '../videoFrames';

const ITEM = [{ url: '/gm/1/stream', name: 'clip.mp4' }];

/** jsdom <video>: give it a duration and a settable, event-firing
 *  currentTime; jsdom <canvas>: stub getContext + toBlob. */
function stubMediaSurface() {
  Object.defineProperty(HTMLMediaElement.prototype, 'duration', {
    configurable: true,
    get() {
      return 10;
    },
  });
  // videoWidth/Height are HTMLVideoElement-own (jsdom ships a return-0
  // getter there) — define on the subclass or it stays 0.
  Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth', {
    configurable: true,
    get() {
      return 640;
    },
  });
  Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', {
    configurable: true,
    get() {
      return 360;
    },
  });
  let t = 2;
  Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
    configurable: true,
    get() {
      return t;
    },
    set(v: number) {
      t = v;
      this.dispatchEvent(new Event('seeked'));
    },
  });
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLCanvasElement.prototype.getContext = vi.fn(
    () => ({ drawImage: vi.fn() }) as unknown as CanvasRenderingContext2D,
  ) as unknown as HTMLCanvasElement['getContext'];
  HTMLCanvasElement.prototype.toBlob = vi.fn((cb: BlobCallback) =>
    cb(new Blob(['x'], { type: 'image/png' })),
  ) as unknown as HTMLCanvasElement['toBlob'];
}

beforeEach(() => {
  stubMediaSurface();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderVideo() {
  return render(
    <OutputLightbox
      items={ITEM}
      index={0}
      kind="video"
      onIndexChange={vi.fn()}
      onClose={vi.fn()}
    />,
  );
}

describe('video lightbox frame stepping (P2-8)', () => {
  it('does not autoplay — the preview waits for the user (Infinite parity)', () => {
    renderVideo();
    const video = screen.getByTestId('lightbox-video') as HTMLVideoElement;
    expect(video.autoplay).toBe(false);
  });

  it('wheel-down steps one frame forward and pauses', () => {
    renderVideo();
    const video = screen.getByTestId('lightbox-video') as HTMLVideoElement;
    const stage = screen.getByTestId('lightbox-stage');
    fireEvent.wheel(stage, { deltaY: 120 });
    expect(video.pause).toHaveBeenCalled();
    expect(video.currentTime).toBeCloseTo(2 + FRAME_STEP_SECONDS, 5);
  });

  it('ArrowLeft steps one frame back instead of switching items', () => {
    renderVideo();
    const video = screen.getByTestId('lightbox-video') as HTMLVideoElement;
    fireEvent.keyDown(screen.getByTestId('output-lightbox'), { key: 'ArrowLeft' });
    expect(video.currentTime).toBeCloseTo(2 - FRAME_STEP_SECONDS, 5);
  });

  it('exports the current frame as a PNG via the shared blob download', async () => {
    renderVideo();
    fireEvent.click(screen.getByRole('button', { name: 'Current Frame' }));
    await vi.waitFor(() => expect(downloadBlob).toHaveBeenCalled());
    const [, name] = downloadBlob.mock.calls[0];
    expect(String(name)).toMatch(/current-frame\.png$/);
  });

  it('offers First / Current / Last frame export keys', () => {
    renderVideo();
    expect(screen.getByRole('button', { name: 'First Frame' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Current Frame' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Last Frame' })).toBeTruthy();
  });
});
