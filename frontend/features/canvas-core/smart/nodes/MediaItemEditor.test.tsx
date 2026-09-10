/**
 * MediaItemEditor (B3) — every commit APPENDS a product onto the card.
 * Crop / outpaint / split derive from the item's OWN url (no promote), so
 * they work on any image; brush / mask / resize bake client-side.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return {
    ...actual,
    deriveCanvasCrop: vi.fn(async () => ({
      id: '901',
      url: '/api/v1/generated-media/901/cover',
      kind: 'image',
      row: null,
      col: null,
    })),
  };
});
vi.mock('../mediaImport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../mediaImport')>();
  return { ...actual, importCanvasMedia: vi.fn() };
});

import { ApiError } from '../../../../services/apiClient';
import { deriveCanvasCrop } from '../../services/canvasService';
import { MediaItemEditor } from './MediaItemEditor';

const ITEM = { url: '/api/v1/generated-media/5/cover', kind: 'image' as const, name: 'a.png' };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderEditor(
  canvasId: string | null,
  mode: 'crop' | 'preview' = 'crop',
  onAppend = vi.fn(),
  onClose = vi.fn(),
) {
  render(
    <MediaItemEditor
      canvasId={canvasId}
      nodeId="m1"
      item={ITEM}
      mode={mode}
      onClose={onClose}
      onAppend={onAppend}
    />,
  );
  return { onAppend, onClose };
}

describe('MediaItemEditor', () => {
  it('crop derives from the item url and appends the durable product', async () => {
    const { onAppend, onClose } = renderEditor('4242');
    fireEvent.click(screen.getByTestId('editor-apply'));
    await waitFor(() => expect(onAppend).toHaveBeenCalledTimes(1));
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      '4242',
      '/api/v1/generated-media/5/cover',
      expect.objectContaining({ width: 1, height: 1 }),
      { nodeId: 'm1' },
    );
    expect(onAppend).toHaveBeenCalledWith({
      url: '/api/v1/generated-media/901/cover',
      kind: 'image',
      name: 'a.png',
      id: '901',
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('offers derive tabs immediately — no promote round trip', () => {
    renderEditor('4242');
    expect(screen.getByTestId('editor-tab-crop')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-split')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-outpaint')).toBeInTheDocument();
  });

  it('without a canvas only the client-side tabs remain', () => {
    renderEditor(null, 'preview');
    expect(screen.queryByTestId('editor-tab-crop')).toBeNull();
    expect(screen.getByTestId('editor-tab-brush')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-resize')).toBeInTheDocument();
  });

  it('a refused derive shows the server message and keeps the editor open', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new ApiError('source image not found', 404),
    );
    const { onAppend, onClose } = renderEditor('4242');
    fireEvent.click(screen.getByTestId('editor-apply'));
    const banner = await screen.findByTestId('editor-commit-error');
    expect(banner.textContent).toBe('source image not found');
    // Inside the portalled dialog, so it is visible above the overlay.
    expect(
      within(screen.getByTestId('unified-image-editor')).getByTestId('editor-commit-error'),
    ).toBe(banner);
    expect(onAppend).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
