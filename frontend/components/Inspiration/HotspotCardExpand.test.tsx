import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
}));

import { HotspotCardExpand } from './HotspotCardExpand';

const HS = (over = {}) => ({
  id: '42',
  title: 'Silent vlog 2.1B',
  tags: [],
  source_label: 'DOUYIN',
  heat: 98.4,
  origin_url: 'https://x',
  summary: 'short summary',
  ...over,
});

const renderExpand = (over = {}, handlers = {}) =>
  render(
    <HotspotCardExpand
      hotspot={HS(over)}
      onSaveAsNote={vi.fn()}
      onParse={vi.fn()}
      onNotInterested={vi.fn()}
      {...handlers}
    />,
  );

describe('HotspotCardExpand', () => {
  beforeEach(() => {
    getHotspot.mockReset();
  });

  it('renders 2 skeleton shimmer lines while the fetch is pending', () => {
    getHotspot.mockReturnValue(new Promise(() => {})); // never resolves
    const { container } = renderExpand();
    expect(container.querySelectorAll('.animate-pulse').length).toBe(2);
  });

  it('shows the full body when it differs from the summary', async () => {
    getHotspot.mockResolvedValue(HS({ content_translated: 'A much longer body paragraph.' }));
    renderExpand();
    expect(await screen.findByText('A much longer body paragraph.')).toBeTruthy();
  });

  it('suppresses the body when it is identical to the summary', async () => {
    getHotspot.mockResolvedValue(HS({ ai_summary: 'same text', content_original: 'same text' }));
    renderExpand();
    // Actions confirm the fetch resolved; the duplicated body must not appear.
    await screen.findByText('Save as note');
    expect(screen.queryByText('same text')).toBeNull();
  });

  it('shows an inline error with retry when the fetch fails, then recovers', async () => {
    getHotspot.mockRejectedValueOnce(new Error('boom'));
    renderExpand();
    const retry = await screen.findByText('Retry');
    expect(screen.getByText('Failed to load details')).toBeTruthy();

    getHotspot.mockResolvedValueOnce(HS({ content_translated: 'recovered body' }));
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText('recovered body')).toBeTruthy());
    expect(screen.queryByText('Failed to load details')).toBeNull();
  });

  it('fires the action handlers', async () => {
    getHotspot.mockResolvedValue(HS());
    const onSaveAsNote = vi.fn();
    const onParse = vi.fn();
    const onNotInterested = vi.fn();
    renderExpand({}, { onSaveAsNote, onParse, onNotInterested });
    fireEvent.click(await screen.findByText('Save as note'));
    fireEvent.click(screen.getByText('Parse'));
    fireEvent.click(screen.getByText('Not interested'));
    expect(onSaveAsNote).toHaveBeenCalledWith(expect.objectContaining({ id: '42' }));
    expect(onParse).toHaveBeenCalledWith(expect.objectContaining({ id: '42' }));
    expect(onNotInterested).toHaveBeenCalledWith(expect.objectContaining({ id: '42' }));
  });
});
