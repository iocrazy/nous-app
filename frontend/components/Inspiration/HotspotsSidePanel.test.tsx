import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspots = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  setHotspotState: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { HotspotsSidePanel } from './HotspotsSidePanel';

const HS = (id: string) => ({ id, title: `T${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO' });

describe('HotspotsSidePanel', () => {
  beforeEach(() => {
    getHotspots.mockReset();
    getHotspots.mockResolvedValue([HS('1'), HS('2'), HS('3'), HS('4')]);
  });

  it('shows top 3 ranked rows', async () => {
    render(<HotspotsSidePanel day={null} onSaveAsNote={vi.fn()} onOpenAll={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('T1')).toBeTruthy());
    expect(screen.getByText('T3')).toBeTruthy();
    expect(screen.queryByText('T4')).toBeNull();
  });

  it('+ button saves the row as a note', async () => {
    const onSaveAsNote = vi.fn();
    render(<HotspotsSidePanel day={null} onSaveAsNote={onSaveAsNote} onOpenAll={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('T1')).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
    expect(onSaveAsNote).toHaveBeenCalledWith(expect.objectContaining({ id: '1' }));
  });

  it('All hotspots link opens the tab', async () => {
    const onOpenAll = vi.fn();
    render(<HotspotsSidePanel day={null} onSaveAsNote={vi.fn()} onOpenAll={onOpenAll} />);
    await waitFor(() => expect(screen.getByText('T1')).toBeTruthy());
    fireEvent.click(screen.getByText(/all hotspots/i));
    expect(onOpenAll).toHaveBeenCalled();
  });
});
