/**
 * MediaItemEditor (B3) — promote-then-derive pipeline for media items.
 * A crop commit derives against the promoted resource id and APPENDS the
 * minted durable item; promote failure hides the derive tabs (honest
 * degradation, Brush/Resize stay).
 */

import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../mediaEditBridge', () => ({
  ensureResourceId: vi.fn(async () => 'res-1'),
}));
vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return { ...actual, deriveCrop: vi.fn(async () => ({ id: 'res-2' })) };
});
vi.mock('../mediaImport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../mediaImport')>();
  return {
    ...actual,
    importResourceAsCanvasMedia: vi.fn(async () => ({
      url: '/api/v1/generated-media/77/file',
      kind: 'image',
    })),
  };
});

import { ensureResourceId } from '../mediaEditBridge';
import { deriveCrop } from '../../services/canvasService';
import { importResourceAsCanvasMedia } from '../mediaImport';
import { MediaItemEditor } from './MediaItemEditor';

const ITEM = { url: '/api/v1/generated-media/5/file', kind: 'image' as const, name: 'a.png' };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('MediaItemEditor', () => {
  it('crop commit derives on the promoted resource and appends the minted item', async () => {
    const onAppend = vi.fn();
    const onClose = vi.fn();
    render(
      <MediaItemEditor
        canvasId="1"
        nodeId="m1"
        item={ITEM}
        mode="crop"
        onClose={onClose}
        onAppend={onAppend}
      />,
    );
    // Wait for the promote to resolve so the crop channel exists.
    await waitFor(() =>
      expect(screen.getByTestId('editor-tab-crop')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('editor-apply'));
    await waitFor(() => expect(onAppend).toHaveBeenCalledTimes(1));
    expect(deriveCrop).toHaveBeenCalledWith('res-1', expect.anything());
    expect(importResourceAsCanvasMedia).toHaveBeenCalledWith('res-2');
    expect(onAppend).toHaveBeenCalledWith({
      url: '/api/v1/generated-media/77/file',
      kind: 'image',
      name: 'a.png',
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('promote failure hides derive tabs, keeps Brush/Resize', async () => {
    (ensureResourceId as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('403'),
    );
    render(
      <MediaItemEditor
        canvasId="1"
        nodeId="m1"
        item={ITEM}
        mode="preview"
        onClose={() => {}}
        onAppend={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByTestId('editor-tab-crop')).toBeNull(),
    );
    expect(screen.getByTestId('editor-tab-brush')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-resize')).toBeInTheDocument();
  });
});
