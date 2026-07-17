import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f, i18n: { language: 'en' } }),
}));

import { HotspotTimeline } from './HotspotTimeline';

const HS = (id: string, over = {}) => ({
  id,
  title: `Topic ${id}`,
  tags: [],
  source_label: 'WEIBO',
  ...over,
});

const dayLabel = (iso: string) =>
  new Date(iso).toLocaleDateString('en', { month: 'long', day: 'numeric' });

describe('HotspotTimeline', () => {
  beforeEach(() => {
    getHotspot.mockReset();
    getHotspot.mockResolvedValue(HS('a'));
  });

  it('groups by local date desc with the undated bucket last', () => {
    const { container } = render(
      <HotspotTimeline
        hotspots={[
          HS('b', { captured_at: '2026-07-16T08:00:00' }),
          HS('u'),
          HS('a', { captured_at: '2026-07-16T10:00:00' }),
          HS('c', { captured_at: '2026-07-15T09:00:00' }),
        ]}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
        applyState={vi.fn()}
      />,
    );
    const titles = Array.from(container.querySelectorAll('h4')).map((h) => h.textContent);
    // Day 2026-07-16 first (a@10 before b@08), then 2026-07-15 (c), undated (u) last.
    expect(titles).toEqual(['Topic a', 'Topic b', 'Topic c', 'Topic u']);
  });

  it('collapsing a date header hides only that group rows', () => {
    render(
      <HotspotTimeline
        hotspots={[
          HS('a', { captured_at: '2026-07-16T10:00:00' }),
          HS('b', { captured_at: '2026-07-16T08:00:00' }),
          HS('c', { captured_at: '2026-07-15T09:00:00' }),
        ]}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
        applyState={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(dayLabel('2026-07-16T10:00:00')));
    expect(screen.queryByText('Topic a')).toBeNull();
    expect(screen.queryByText('Topic b')).toBeNull();
    expect(screen.getByText('Topic c')).toBeTruthy();
  });

  it('expands inside the card container and collapses on second click', async () => {
    getHotspot.mockResolvedValue(HS('a', { captured_at: '2026-07-16T10:00:00', content_translated: 'DETAIL BODY' }));
    render(
      <HotspotTimeline
        hotspots={[HS('a', { captured_at: '2026-07-16T10:00:00' })]}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
        applyState={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('Topic a'));
    await waitFor(() => expect(getHotspot).toHaveBeenCalledWith('a'));
    const body = await screen.findByText('DETAIL BODY');
    // The expansion lives INSIDE the single card container (one unit), not a sibling.
    const card = screen.getByText('Topic a').closest('[role="button"]') as HTMLElement;
    expect(card.contains(body)).toBe(true);

    fireEvent.click(screen.getAllByText('Topic a')[0]);
    await waitFor(() => expect(screen.queryByText('DETAIL BODY')).toBeNull());
  });

  it('clicking inside the expanded area does not collapse the card', async () => {
    getHotspot.mockResolvedValue(HS('a', { captured_at: '2026-07-16T10:00:00', content_translated: 'DETAIL BODY' }));
    render(
      <HotspotTimeline
        hotspots={[HS('a', { captured_at: '2026-07-16T10:00:00' })]}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
        applyState={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('Topic a'));
    const body = await screen.findByText('DETAIL BODY');
    fireEvent.click(body);
    // Still expanded — the stopPropagation wrapper prevented a toggle.
    expect(screen.getByText('DETAIL BODY')).toBeTruthy();
  });

  it('clicking the save star toggles state without expanding the card', async () => {
    const applyState = vi.fn().mockResolvedValue(undefined);
    render(
      <HotspotTimeline
        hotspots={[HS('a', { captured_at: '2026-07-16T10:00:00' })]}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
        applyState={applyState}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(applyState).toHaveBeenCalledWith(expect.objectContaining({ id: 'a' }), { is_saved: true }),
    );
    expect(getHotspot).not.toHaveBeenCalled();
  });

  it('read items render the dimmed card', () => {
    render(
      <HotspotTimeline
        hotspots={[HS('a', { captured_at: '2026-07-16T10:00:00', is_read: true })]}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
        applyState={vi.fn()}
      />,
    );
    const card = screen.getByText('Topic a').closest('[role="button"]') as HTMLElement;
    expect(card.className).toContain('opacity-60');
  });
});
