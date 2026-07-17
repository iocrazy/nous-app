import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f, i18n: { language: 'en' } }),
}));

import { HotspotsWorkspace } from './HotspotsWorkspace';

const HS = (id: string, over = {}) => ({
  id, title: `Topic ${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO',
  captured_at: '2026-07-16T10:00:00', ...over,
});

const VIEW_KEY = 'inspiration.hotspotsView';

const renderWorkspace = () =>
  render(
    <HotspotsWorkspace
      hotspots={[HS('1'), HS('2')]}
      loading={false}
      applyState={vi.fn()}
      activeCategory={null}
      onSaveAsNote={vi.fn()}
      onParse={vi.fn()}
    />,
  );

describe('HotspotsWorkspace view toggle', () => {
  beforeEach(() => {
    getHotspot.mockReset();
    getHotspot.mockResolvedValue(HS('1'));
    localStorage.clear();
  });

  it('defaults to the ranked view', () => {
    renderWorkspace();
    // Ranked view auto-selects the top hotspot → detail action row is present.
    expect(screen.getByText('Save as note')).toBeTruthy();
  });

  it('switching to Timeline persists the choice and drops the ranked detail pane', () => {
    renderWorkspace();
    fireEvent.click(screen.getByRole('button', { name: 'Timeline' }));
    expect(localStorage.getItem(VIEW_KEY)).toBe('timeline');
    // Timeline has no expanded card yet → no inline detail action row.
    expect(screen.queryByText('Save as note')).toBeNull();
  });

  it('reads the persisted timeline view on mount and can switch back to ranked', () => {
    localStorage.setItem(VIEW_KEY, 'timeline');
    renderWorkspace();
    expect(screen.queryByText('Save as note')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Ranked' }));
    expect(localStorage.getItem(VIEW_KEY)).toBe('ranked');
    expect(screen.getByText('Save as note')).toBeTruthy();
  });
});
