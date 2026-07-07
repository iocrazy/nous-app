import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspots = vi.fn();
const setHotspotState = vi.fn();
const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  setHotspotState: (...a: unknown[]) => setHotspotState(...a),
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { HotspotsWorkspace } from './HotspotsWorkspace';

const HS = (id: string, over = {}) => ({ id, title: `Topic ${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO', ...over });

describe('HotspotsWorkspace', () => {
  beforeEach(() => {
    getHotspots.mockReset();
    getHotspot.mockReset();
    getHotspots.mockResolvedValue([HS('1'), HS('2'), HS('3')]);
    getHotspot.mockResolvedValue(HS('1'));
  });

  it('renders the ranked list for the day', async () => {
    render(<HotspotsWorkspace day="2026-07-07" onSaveAsNote={vi.fn()} onParse={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Topic 1')).toBeTruthy());
    expect(screen.getByText('Topic 3')).toBeTruthy();
  });

  it('clicking a row selects it into the detail pane', async () => {
    render(<HotspotsWorkspace day={null} onSaveAsNote={vi.fn()} onParse={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Topic 2')).toBeTruthy());
    fireEvent.click(screen.getByText('Topic 2'));
    await waitFor(() => expect(getHotspot).toHaveBeenCalledWith('2'));
  });

  it('empty day shows empty state', async () => {
    getHotspots.mockResolvedValue([]);
    render(<HotspotsWorkspace day={null} onSaveAsNote={vi.fn()} onParse={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/no hotspots/i)).toBeTruthy());
  });
});
