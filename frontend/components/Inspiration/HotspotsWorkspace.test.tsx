import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { HotspotsWorkspace } from './HotspotsWorkspace';

const HS = (id: string, over = {}) => ({ id, title: `Topic ${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO', ...over });

describe('HotspotsWorkspace', () => {
  beforeEach(() => {
    getHotspot.mockReset();
    getHotspot.mockResolvedValue(HS('1'));
  });

  it('renders the ranked list from the hotspots prop', async () => {
    render(
      <HotspotsWorkspace
        hotspots={[HS('1'), HS('2'), HS('3')]}
        loading={false}
        applyState={vi.fn()}
        activeCategory={null}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: /Topic 1/ })).toBeTruthy());
    expect(screen.getByRole('button', { name: /Topic 3/ })).toBeTruthy();
  });

  it('clicking a row selects it into the detail pane', async () => {
    render(
      <HotspotsWorkspace
        hotspots={[HS('1'), HS('2'), HS('3')]}
        loading={false}
        applyState={vi.fn()}
        activeCategory={null}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: /Topic 2/ })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /Topic 2/ }));
    await waitFor(() => expect(getHotspot).toHaveBeenCalledWith('2'));
  });

  it('empty hotspots list shows empty state', async () => {
    render(
      <HotspotsWorkspace
        hotspots={[]}
        loading={false}
        applyState={vi.fn()}
        activeCategory={null}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText(/no hotspots/i)).toBeTruthy());
  });

  it('hides hotspots outside a non-null activeCategory', async () => {
    render(
      <HotspotsWorkspace
        hotspots={[HS('1', { category: 'food' }), HS('2', { category: 'music' })]}
        loading={false}
        applyState={vi.fn()}
        activeCategory="food"
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: /Topic 1/ })).toBeTruthy());
    expect(screen.queryByRole('button', { name: /Topic 2/ })).toBeNull();
  });

  it('null activeCategory shows all hotspots', async () => {
    render(
      <HotspotsWorkspace
        hotspots={[HS('1', { category: 'food' }), HS('2', { category: 'music' })]}
        loading={false}
        applyState={vi.fn()}
        activeCategory={null}
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: /Topic 1/ })).toBeTruthy());
    expect(screen.getByRole('button', { name: /Topic 2/ })).toBeTruthy();
  });

  it('already-hidden hotspots stay excluded regardless of category', async () => {
    render(
      <HotspotsWorkspace
        hotspots={[HS('1', { category: 'food', is_hidden: true }), HS('2', { category: 'food' })]}
        loading={false}
        applyState={vi.fn()}
        activeCategory="food"
        onSaveAsNote={vi.fn()}
        onParse={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: /Topic 2/ })).toBeTruthy());
    expect(screen.queryByRole('button', { name: /Topic 1/ })).toBeNull();
  });
});
