/**
 * The library as a picker: searches, pages, multi-selects within the room the
 * pool has left, and never re-adds what is already a reference.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';

const { listCoverTemplates } = vi.hoisted(() => ({ listCoverTemplates: vi.fn() }));
vi.mock('../../../services/coverTemplateService', () => ({ listCoverTemplates }));
vi.mock('./AddCoverTemplateModal', () => ({ AddCoverTemplateModal: () => null }));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { CoverTemplatePickerModal } from './CoverTemplatePickerModal';

const FOLDER = { folder_id: '1', name: '封面', adopted: true };
const tpl = (n: number) => ({
  resource_id: String(n), name: `t${n}.png`, mime_type: 'image/png',
  thumb_url: `/api/v1/resources/${n}/cover`, usage_count: 0, last_used_at: null,
});

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en', fallbackLng: 'en', resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false }, react: { useSuspense: false },
  });
  return inst;
};

function renderPicker(over: Partial<React.ComponentProps<typeof CoverTemplatePickerModal>> = {}) {
  const onPick = vi.fn();
  const onClose = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverTemplatePickerModal open scopeId="s" room={2} onClose={onClose} onPick={onPick} {...over} />
    </I18nextProvider>,
  );
  return { onPick, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  listCoverTemplates.mockResolvedValue({ folder: FOLDER, items: [tpl(1), tpl(2), tpl(3)], total: 5, limit: 48, offset: 0 });
});

describe('CoverTemplatePickerModal', () => {
  it('lists a page, says the total, and offers more', async () => {
    renderPicker();
    expect(await screen.findByTestId('cover-picker-item-1')).toBeTruthy();
    expect(screen.getByText('“封面” · 5 pictures')).toBeTruthy();
    expect(screen.getByTestId('cover-picker-more').textContent).toContain('2 left');

    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [tpl(4), tpl(5)], total: 5, limit: 48, offset: 3 });
    fireEvent.click(screen.getByTestId('cover-picker-more'));
    expect(await screen.findByTestId('cover-picker-item-5')).toBeTruthy();
    expect(listCoverTemplates).toHaveBeenLastCalledWith({ q: '', limit: 48, offset: 3 });
  });

  it('searches by name', async () => {
    renderPicker();
    await screen.findByTestId('cover-picker-item-1');
    fireEvent.change(screen.getByTestId('cover-picker-search'), { target: { value: 'bold' } });
    await waitFor(() => expect(listCoverTemplates).toHaveBeenLastCalledWith({ q: 'bold', limit: 48, offset: 0 }));
  });

  it('multi-selects up to the room left, skips what is already a reference, and hands the picks back', async () => {
    const { onPick, onClose } = renderPicker({ alreadyIn: ['1'] });
    await screen.findByTestId('cover-picker-item-1');
    expect((screen.getByTestId('cover-picker-item-1') as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByTestId('cover-picker-item-2'));
    fireEvent.click(screen.getByTestId('cover-picker-item-3'));
    // room=2 → a third pick would be refused; both 2 and 3 fit.
    expect(screen.getByTestId('cover-picker-count').textContent).toContain('2 picked');

    fireEvent.click(screen.getByTestId('cover-picker-confirm'));
    expect(onPick).toHaveBeenCalledWith([expect.objectContaining({ resource_id: '2' }), expect.objectContaining({ resource_id: '3' })]);
    expect(onClose).toHaveBeenCalled();
  });
});
