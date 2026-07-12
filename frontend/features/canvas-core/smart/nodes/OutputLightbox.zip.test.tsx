// features/canvas-core/smart/nodes/OutputLightbox.zip.test.tsx
// Download-All zip (P2-7): the multi-image download packs server-side into
// one archive; a non-2xx zip response falls back to the per-file loop so
// the deploy-skew window (frontend up before backend) never breaks.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const downloadCanvasAssetsZip = vi.fn();
vi.mock('../../services/canvasGenerationService', () => ({
  downloadCanvasAssetsZip: (...a: unknown[]) => downloadCanvasAssetsZip(...a),
}));

const downloadUrl = vi.fn();
const downloadBlob = vi.fn();
vi.mock('../downloadMedia', () => ({
  downloadUrl: (...a: unknown[]) => downloadUrl(...a),
  downloadBlob: (...a: unknown[]) => downloadBlob(...a),
  downloadName: (item: { name?: string }) => item.name ?? 'x',
}));

import { OutputLightbox } from './OutputLightbox';

const ITEMS = [
  { url: '/api/v1/generated-media/1/cover', name: 'a.png' },
  { url: '/api/v1/generated-media/2/cover', name: 'b.png' },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderBox() {
  return render(
    <OutputLightbox
      items={ITEMS}
      index={0}
      kind="image"
      onIndexChange={vi.fn()}
      onClose={vi.fn()}
    />,
  );
}

describe('Download All → server zip (P2-7)', () => {
  it('packs the batch into one zip and saves it', async () => {
    downloadCanvasAssetsZip.mockResolvedValue(new Blob(['zip'], { type: 'application/zip' }));
    renderBox();
    fireEvent.click(screen.getByRole('button', { name: 'Download All' }));
    await vi.waitFor(() => expect(downloadBlob).toHaveBeenCalled());
    expect(downloadCanvasAssetsZip).toHaveBeenCalledWith(
      expect.stringMatching(/\.zip$/),
      [
        { url: ITEMS[0].url, name: 'a.png' },
        { url: ITEMS[1].url, name: 'b.png' },
      ],
    );
    // No per-file fallback when the zip succeeded.
    expect(downloadUrl).not.toHaveBeenCalled();
  });

  it('falls back to per-file downloads when the zip endpoint errors', async () => {
    downloadCanvasAssetsZip.mockRejectedValue(new Error('HTTP 404'));
    downloadUrl.mockResolvedValue(undefined);
    renderBox();
    fireEvent.click(screen.getByRole('button', { name: 'Download All' }));
    await vi.waitFor(() => expect(downloadUrl).toHaveBeenCalledTimes(2));
    expect(downloadUrl).toHaveBeenNthCalledWith(1, ITEMS[0], 0);
    expect(downloadUrl).toHaveBeenNthCalledWith(2, ITEMS[1], 1);
  });

  it('single Download still uses the per-file path (not the zip)', async () => {
    downloadUrl.mockResolvedValue(undefined);
    renderBox();
    fireEvent.click(screen.getByRole('button', { name: /^Download$/ }));
    await vi.waitFor(() => expect(downloadUrl).toHaveBeenCalledTimes(1));
    expect(downloadCanvasAssetsZip).not.toHaveBeenCalled();
  });
});
