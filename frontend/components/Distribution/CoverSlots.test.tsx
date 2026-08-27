/**
 * The cover section is two slots and nothing else. Pinned:
 *   1. BOTH slots are real buttons that open the studio on their own tab —
 *      the horizontal one used to be a dead, unclickable box.
 *   2. A filled slot shows the cover and offers a clear control; an empty one
 *      does not offer clear (nothing to clear).
 *   3. Clear empties only that slot.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import enJson from '../../public/locales/en.json';

vi.mock('../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    auth: { getSession: () => Promise.resolve({ data: { session: { access_token: 'jwt' } } }) },
  }),
}));
vi.mock('../../services/resourceService', () => ({
  getResourceFileUrl: (id: string, token?: string) =>
    `https://api.test/api/v1/resources/${id}/file${token ? `?token=${token}` : ''}`,
}));

import { CoverSlots } from './CoverSlots';

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
};

function renderSlots(value: { vertical?: string; horizontal?: string } | null) {
  const onOpen = vi.fn();
  const onClear = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverSlots value={value} onOpen={onOpen} onClear={onClear} />
    </I18nextProvider>,
  );
  return { onOpen, onClear };
}

describe('CoverSlots', () => {
  it('both slots open the studio on their own tab', () => {
    const { onOpen } = renderSlots(null);
    fireEvent.click(screen.getByTestId('open-cover-studio'));
    expect(onOpen).toHaveBeenLastCalledWith('vertical');
    fireEvent.click(screen.getByTestId('open-cover-studio-h'));
    expect(onOpen).toHaveBeenLastCalledWith('horizontal');
    expect(screen.getAllByText('Choose cover')).toHaveLength(2);
  });

  it('a filled slot shows the cover and can be cleared on its own', async () => {
    const { onClear } = renderSlots({ vertical: '7001' });
    const img = (await screen.findByAltText('Vertical 3:4')) as HTMLImageElement;
    expect(img.src).toContain('/api/v1/resources/7001/file');
    expect(screen.getByTestId('cover-clear-v')).toBeTruthy();
    expect(screen.queryByTestId('cover-clear-h')).toBeNull();

    fireEvent.click(screen.getByTestId('cover-clear-v'));
    expect(onClear).toHaveBeenCalledWith('vertical');
  });
});

describe('CoverSlots — blocked without a video', () => {
  it('dims the tiles, and a click reports the reason instead of opening', () => {
    const onOpen = vi.fn();
    const onBlocked = vi.fn();
    render(
      <I18nextProvider i18n={makeI18n()}>
        <CoverSlots value={null} onOpen={onOpen} onClear={vi.fn()} blockedReason="Select a video above first" onBlocked={onBlocked} />
      </I18nextProvider>,
    );
    const tile = screen.getByTestId('open-cover-studio');
    expect(tile.className).toContain('blocked');
    expect(tile.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(tile);
    fireEvent.click(screen.getByTestId('open-cover-studio-h'));
    expect(onOpen).not.toHaveBeenCalled();
    expect(onBlocked).toHaveBeenCalledTimes(2);
  });
});
