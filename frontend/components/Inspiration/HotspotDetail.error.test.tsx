import { beforeEach, describe, expect, it, vi } from 'vitest';
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

describe('HotspotDetail load error', () => {
  beforeEach(() => {
    getHotspot.mockReset();
    getHotspot.mockResolvedValue(HS());
  });

  it('shows an inline error with retry when the detail load fails, then recovers', async () => {
    getHotspot.mockRejectedValueOnce(new Error('boom'));
    render(
      <HotspotDetail hotspot={HS()} onSaveAsNote={vi.fn()} onParse={vi.fn()} onNotInterested={vi.fn()} />,
    );
    // Stub data still visible.
    expect(screen.getByText('Silent vlog 2.1B')).toBeTruthy();
    const retry = await screen.findByText('Retry');
    expect(screen.getByText('Failed to load details')).toBeTruthy();

    getHotspot.mockResolvedValueOnce(HS({ ai_summary: 'recovered summary' }));
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText('recovered summary')).toBeTruthy());
    expect(screen.queryByText('Failed to load details')).toBeNull();
  });
});
