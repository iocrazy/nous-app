/**
 * The vertical cover tile is the entry to Cover Studio — the platform's own
 * publish form opens its cover editor from exactly these tiles. Two properties:
 *
 *   1. With `onOpenStudio`, the vertical tile is a real BUTTON that opens the
 *      studio. A tile that only looks clickable is the "Coming in D4" mistake.
 *   2. Without it, nothing changes — the tile stays the plain box it was, so
 *      callers that never wire the studio are untouched.
 *
 * The horizontal tile never opens the studio: its style is 3:4 only.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import enJson from '../../public/locales/en.json';

vi.mock('../../services/distributionService', () => ({
  extractCoverFrames: vi.fn(),
  selectCoverFrame: vi.fn(),
  fetchMusicCharts: vi.fn().mockResolvedValue({ charts: [], last_success_at: null, stale: true, never_harvested: true, ttl_hours: 24 }),
  refreshMusicCharts: vi.fn(),
  musicChartKey: (c: { category_kind: string; category_id: string }) => `${c.category_kind}:${c.category_id}`,
}));
vi.mock('../../services/resourceService', () => ({
  getResourceFileUrl: (id: string) => `/file/${id}`,
}));
vi.mock('../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    auth: { getSession: () => Promise.resolve({ data: { session: { access_token: 'jwt' } } }) },
    channel: () => ({ on: () => ({ subscribe: () => ({}) }), subscribe: () => ({}) }),
    removeChannel: vi.fn(),
    from: () => ({ select: () => ({ eq: () => ({ maybeSingle: () => Promise.resolve({ data: null, error: null }) }) }) }),
  }),
}));

import { CoverPicker } from './CoverPicker';

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en', fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false }, react: { useSuspense: false },
  });
  return inst;
};

describe('CoverPicker — studio entry', () => {
  it('opens the studio from the vertical tile when wired', () => {
    const onOpenStudio = vi.fn();
    render(
      <I18nextProvider i18n={makeI18n()}>
        <CoverPicker sources={[{ id: '900', filename: 'clip-a.mp4' }]} value={null} onChange={vi.fn()} onOpenStudio={onOpenStudio} />
      </I18nextProvider>,
    );

    const tile = screen.getByTestId('cover-slot-open-studio');
    expect(tile.tagName).toBe('BUTTON');
    expect(tile.textContent).toContain('Choose cover');
    fireEvent.click(tile);

    expect(onOpenStudio).toHaveBeenCalledTimes(1);
  });

  it('leaves the tile a plain box when the studio is not wired', () => {
    render(
      <I18nextProvider i18n={makeI18n()}>
        <CoverPicker sources={[{ id: '900', filename: 'clip-a.mp4' }]} value={null} onChange={vi.fn()} />
      </I18nextProvider>,
    );

    // Proof the picker rendered at all, so the negative below is not vacuous.
    expect(screen.getByText('Vertical 3:4')).toBeTruthy();
    expect(screen.queryByTestId('cover-slot-open-studio')).toBeNull();
  });
});
