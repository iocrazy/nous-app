/**
 * EntityAssetStrip (CC5) — the bible-card asset strip: fetches generations
 * backlinked to one entity, renders thumbnails, opens a lightbox on click.
 */

import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchEntityGenerations = vi.fn();
vi.mock('../../services/generatedMediaService', () => ({
  fetchEntityGenerations: (...a: unknown[]) => fetchEntityGenerations(...a),
  generatedMediaCoverUrl: (id: string) => `/api/v1/generated-media/${id}/cover`,
  generatedMediaStreamUrl: (id: string) => `/api/v1/generated-media/${id}/stream`,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

import { EntityAssetStrip } from './EntityAssetStrip';

const IMAGE_ITEM = {
  id: '901',
  media_kind: 'image',
  origin_kind: 'canvas_run',
  created_at: '2026-07-13T00:00:00Z',
};
const VIDEO_ITEM = {
  id: '902',
  media_kind: 'video',
  origin_kind: 'canvas_run',
  created_at: '2026-07-13T00:00:00Z',
};

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe('EntityAssetStrip', () => {
  it('queries by entity and renders image thumbnails', async () => {
    fetchEntityGenerations.mockResolvedValue({
      items: [IMAGE_ITEM],
      next_cursor: null,
    });
    render(<EntityAssetStrip entityKind="character" entityId="42" />);
    await waitFor(() =>
      expect(fetchEntityGenerations).toHaveBeenCalledWith('character', '42'),
    );
    const img = (await screen.findByRole('img')) as HTMLImageElement;
    expect(img.src).toContain('/api/v1/generated-media/901/cover');
  });

  it('shows the empty hint when nothing is linked', async () => {
    fetchEntityGenerations.mockResolvedValue({ items: [], next_cursor: null });
    render(<EntityAssetStrip entityKind="prop" entityId="7" />);
    expect(
      await screen.findByText('Generated assets appear here'),
    ).toBeTruthy();
  });

  it('opens a lightbox on thumbnail click and closes it again', async () => {
    fetchEntityGenerations.mockResolvedValue({
      items: [IMAGE_ITEM, VIDEO_ITEM],
      next_cursor: null,
    });
    render(<EntityAssetStrip entityKind="location" entityId="5" />);
    const thumb = await screen.findByRole('img');
    fireEvent.click(thumb);
    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelector('img')?.src).toContain('/901/cover');
    fireEvent.click(screen.getByLabelText('Close'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('renders a video tile that opens a <video> lightbox', async () => {
    fetchEntityGenerations.mockResolvedValue({
      items: [VIDEO_ITEM],
      next_cursor: null,
    });
    render(<EntityAssetStrip entityKind="character" entityId="42" />);
    const tile = await screen.findByLabelText('Video asset');
    fireEvent.click(tile);
    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelector('video')?.src).toContain(
      '/api/v1/generated-media/902/stream',
    );
  });

  it('swallows fetch errors into the empty hint (no crash)', async () => {
    fetchEntityGenerations.mockRejectedValue(new Error('boom'));
    render(<EntityAssetStrip entityKind="character" entityId="42" />);
    expect(
      await screen.findByText('Generated assets appear here'),
    ).toBeTruthy();
  });
});
