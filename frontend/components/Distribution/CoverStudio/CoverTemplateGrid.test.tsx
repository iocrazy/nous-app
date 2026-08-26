/**
 * The Templates card — the properties that are not obvious from reading it.
 *
 *   1. "could not load" and "you have none" are DIFFERENT boxes. Rendering a
 *      failed request as an empty grid tells the user they own nothing, which
 *      is a claim the component cannot support when nothing came back.
 *   2. The tile's <img> src is the server-derived `thumb_url`, unmodified
 *      apart from the API origin.
 *   3. Which folder backs the library is SAID on the card — and an adopted
 *      folder (the user's own “封面”) is told apart from one we created,
 *      because "your folder is now protected" is news the user should get.
 *   4. There is no remove button: taking a picture out of the library is done
 *      in the library. A second removal path would be the drift.
 *   5. Re-adding a picture already in the folder must not show it twice.
 *
 * ⚠️ Every negative assertion is paired with proof the grid actually rendered —
 * "no second tile appeared" passes vacuously if nothing appeared at all.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';

const { listCoverTemplates } = vi.hoisted(() => ({
  listCoverTemplates: vi.fn(),
}));
vi.mock('../../../services/coverTemplateService', () => ({
  listCoverTemplates,
}));

// The modal has its own file and its own tests; here it only has to be able
// to hand a template back.
let lastOnAdded: ((t: unknown) => void) | null = null;
vi.mock('./AddCoverTemplateModal', () => ({
  AddCoverTemplateModal: ({
    open,
    onAdded,
  }: {
    open: boolean;
    onAdded: (t: unknown) => void;
  }) => {
    lastOnAdded = onAdded;
    return open ? <div data-testid="add-modal-open" /> : null;
  },
}));

vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { CoverTemplateGrid } from './CoverTemplateGrid';

/** Real wire shape: snowflakes are STRINGS; thumb_url is the backend's path. */
const FOLDER = { folder_id: '341588599799820', name: '封面', adopted: true };
const TEMPLATE = {
  resource_id: '341582104263581',
  name: 'bold-headline.png',
  mime_type: 'image/png',
  thumb_url: '/api/v1/resources/341582104263581/cover',
  usage_count: 12,
  last_used_at: null,
};

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return inst;
};

function renderGrid(props: Partial<React.ComponentProps<typeof CoverTemplateGrid>> = {}) {
  return render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverTemplateGrid scopeId="scope-1" {...props} />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  lastOnAdded = null;
});

describe('CoverTemplateGrid', () => {
  it('renders the folder pictures with the backend thumb url and the usage caption', async () => {
    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [TEMPLATE] });
    renderGrid();

    const img = (await screen.findByAltText('bold-headline.png')) as HTMLImageElement;
    expect(img.src).toBe('https://api.test/api/v1/resources/341582104263581/cover');
    expect(screen.getByText('used 12×')).toBeTruthy();
    expect(screen.getByText('1 saved · 0 in use')).toBeTruthy();
  });

  it('says it is the user’s own adopted folder, now protected', async () => {
    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [TEMPLATE] });
    renderGrid();

    const note = await screen.findByTestId('cover-template-folder-note');
    expect(note.textContent).toContain('“封面”');
    expect(note.textContent).toContain('protected system folder');
    expect(note.textContent).toContain('your');
  });

  it('says a created folder is where templates are kept', async () => {
    listCoverTemplates.mockResolvedValueOnce({
      folder: { ...FOLDER, name: 'Covers', adopted: false },
      items: [],
    });
    renderGrid();

    const note = await screen.findByTestId('cover-template-folder-note');
    expect(note.textContent).toContain('“Covers”');
    expect(note.textContent).toMatch(/^Kept in/);
  });

  it('has no remove button — removal lives in the library', async () => {
    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [TEMPLATE] });
    renderGrid();

    await screen.findByAltText('bold-headline.png');
    expect(screen.queryByLabelText(/remove template/i)).toBeNull();
  });

  it('counts in-use tiles by resource id and toggles through the parent', async () => {
    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [TEMPLATE] });
    const onToggle = vi.fn();
    renderGrid({ selectedIds: [TEMPLATE.resource_id], onToggle });

    const tile = await screen.findByTitle('bold-headline.png');
    expect(tile.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('1 saved · 1 in use')).toBeTruthy();
    fireEvent.click(tile);
    expect(onToggle).toHaveBeenCalledWith(TEMPLATE);
  });

  it('ignores clicks on the tile whose reference is being prepared', async () => {
    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [TEMPLATE] });
    const onToggle = vi.fn();
    renderGrid({ busyId: TEMPLATE.resource_id, onToggle });

    const tile = await screen.findByTitle('bold-headline.png');
    expect(tile.getAttribute('aria-busy')).toBe('true');
    fireEvent.click(tile);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('shows "could not load" with a retry, NOT the empty-state copy', async () => {
    listCoverTemplates.mockRejectedValueOnce(new Error('503'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderGrid();

    expect(await screen.findByText('Could not load your templates.')).toBeTruthy();
    expect(screen.queryByText(/No templates yet/)).toBeNull();

    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [TEMPLATE] });
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByAltText('bold-headline.png')).toBeTruthy();
    spy.mockRestore();
  });

  it('shows the empty state only when the folder really is empty', async () => {
    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [] });
    renderGrid();

    expect(await screen.findByText(/No templates yet/)).toBeTruthy();
    expect(screen.queryByText('Could not load your templates.')).toBeNull();
  });

  it('opens the add modal and does not duplicate a picture already in the folder', async () => {
    listCoverTemplates.mockResolvedValueOnce({ folder: FOLDER, items: [TEMPLATE] });
    renderGrid();
    await screen.findByAltText('bold-headline.png');

    fireEvent.click(screen.getByTestId('cover-template-add'));
    expect(screen.getByTestId('add-modal-open')).toBeTruthy();

    act(() => lastOnAdded?.(TEMPLATE));
    await waitFor(() => expect(screen.getAllByAltText('bold-headline.png')).toHaveLength(1));

    act(() => lastOnAdded?.({ ...TEMPLATE, resource_id: '2', name: 'second.png' }));
    expect(await screen.findByAltText('second.png')).toBeTruthy();
    expect(screen.getByText('2 saved · 0 in use')).toBeTruthy();
  });
});
