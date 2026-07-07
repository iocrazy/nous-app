import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
}));

import { HotspotDetail } from './HotspotDetail';

const HS = (over = {}) => ({
  id: '42', title: 'Silent vlog 2.1B', tags: [], source_label: 'DOUYIN',
  category: 'food', heat: 98.4, origin_url: 'https://x', summary: 'short', ...over,
});

describe('HotspotDetail', () => {
  it('shows empty state when no hotspot selected', () => {
    render(<HotspotDetail hotspot={null} onSaveAsNote={vi.fn()} onParse={vi.fn()} onNotInterested={vi.fn()} />);
    expect(screen.getByText(/select a hotspot/i)).toBeTruthy();
  });

  it('renders title, source, category tag, heat', async () => {
    getHotspot.mockResolvedValue(HS({ ai_summary: 'full summary' }));
    render(<HotspotDetail hotspot={HS()} onSaveAsNote={vi.fn()} onParse={vi.fn()} onNotInterested={vi.fn()} />);
    expect(screen.getByText('Silent vlog 2.1B')).toBeTruthy();
    expect(screen.getByText('DOUYIN')).toBeTruthy();
    expect(screen.getByText('#food')).toBeTruthy();
    expect(screen.getByText(/98.4/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText('full summary')).toBeTruthy());
  });

  it('save-as-note action fires with the hotspot', () => {
    getHotspot.mockResolvedValue(HS());
    const onSaveAsNote = vi.fn();
    render(<HotspotDetail hotspot={HS()} onSaveAsNote={onSaveAsNote} onParse={vi.fn()} onNotInterested={vi.fn()} />);
    fireEvent.click(screen.getByText('Save as note'));
    expect(onSaveAsNote).toHaveBeenCalledWith(expect.objectContaining({ id: '42' }));
  });
});
