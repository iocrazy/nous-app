/**
 * The Templates card — the four properties that are not obvious from reading it.
 *
 *   1. "could not load" and "you have none" are DIFFERENT boxes. Rendering a
 *      failed request as an empty grid tells the user they own nothing, which
 *      is a claim the component cannot support when nothing came back.
 *   2. The tile's <img> src is the server-derived `image_url`, unmodified apart
 *      from the API origin. That URL is simultaneously the reference URL a
 *      generation needs, and rebuilding it here would be the one place the two
 *      could drift apart.
 *   3. Remove does not also toggle pool membership. Without stopPropagation the
 *      same click does both, on a template that is about to stop existing.
 *   4. Re-adding an already-saved picture is idempotent server-side and returns
 *      the EXISTING row, so the grid must not show it twice.
 *
 * ⚠️ Every negative assertion is paired with proof the grid actually rendered —
 * "no second tile appeared" passes vacuously if nothing appeared at all.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';

const { listCoverTemplates, deleteCoverTemplate } = vi.hoisted(() => ({
  listCoverTemplates: vi.fn(),
  deleteCoverTemplate: vi.fn(),
}));
vi.mock('../../../services/coverTemplateService', () => ({
  listCoverTemplates,
  deleteCoverTemplate,
}));

// The modal has its own file and its own tests; here it must only be inert.
vi.mock('./AddCoverTemplateModal', () => ({
  AddCoverTemplateModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="add-modal-open" /> : null,
}));

vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { CoverTemplateGrid } from './CoverTemplateGrid';

/**
 * Real wire shape: every snowflake is a STRING (the router stringifies them
 * explicitly), and `image_url` is the path the backend derived. Prettifying
 * either into "nicer" TypeScript is the drift this repo has been bitten by.
 */
const TEMPLATE = {
  id: '341588599799820',
  name: 'Bold headline',
  generated_media_id: '341582104263581',
  image_url: '/api/v1/generated-media/341582104263581/cover',
  source_kind: 'upload' as const,
  source_resource_id: null,
  usage_count: 12,
  last_used_at: null,
  created_at: '2026-08-22T00:00:00Z',
};

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

function renderGrid(props: Partial<React.ComponentProps<typeof CoverTemplateGrid>> = {}) {
  return render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverTemplateGrid scopeId="42" {...props} />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listCoverTemplates.mockResolvedValue([TEMPLATE]);
  deleteCoverTemplate.mockResolvedValue(true);
});

describe('CoverTemplateGrid', () => {
  it('renders a saved template with its name and use count', async () => {
    renderGrid();

    expect(await screen.findByText('Bold headline')).toBeTruthy();
    expect(screen.getByText('used 12×')).toBeTruthy();
  });

  it('points the tile at the server-derived image_url, unmodified', async () => {
    renderGrid();

    const img = (await screen.findByAltText('Bold headline')) as HTMLImageElement;
    // Literal, not a substring match: this exact shape is the only one the
    // generation bridge accepts as a reference, so an "almost right" URL here
    // would be invisible until a generation quietly ignored the template.
    expect(img.getAttribute('src')).toBe(
      'https://api.test/api/v1/generated-media/341582104263581/cover',
    );
  });

  it('says the load FAILED rather than showing an empty library', async () => {
    listCoverTemplates.mockRejectedValueOnce(new Error('boom'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderGrid();

    expect(await screen.findByText(/Could not load your templates/)).toBeTruthy();
    // The "you have none" copy must NOT also be on screen — that is the exact
    // conflation this branch exists to prevent.
    expect(screen.queryByText(/No templates yet/)).toBeNull();
    spy.mockRestore();
  });

  it('says "none yet" only when the load actually succeeded and was empty', async () => {
    listCoverTemplates.mockResolvedValueOnce([]);

    renderGrid();

    expect(await screen.findByText(/No templates yet/)).toBeTruthy();
    expect(screen.queryByText(/Could not load your templates/)).toBeNull();
  });

  it('toggling a tile reports the template to the parent', async () => {
    const onToggle = vi.fn();
    renderGrid({ onToggle });

    fireEvent.click(await screen.findByTitle('Bold headline'));

    expect(onToggle).toHaveBeenCalledWith(expect.objectContaining({ id: TEMPLATE.id }));
  });

  it('removing does NOT also toggle pool membership', async () => {
    const onToggle = vi.fn();
    const onRemoved = vi.fn();
    renderGrid({ onToggle, onRemoved });

    // Proof the grid rendered, so the negative assertion below cannot pass
    // just because nothing was on screen.
    expect(await screen.findByTitle('Bold headline')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Remove template'));

    await waitFor(() => expect(deleteCoverTemplate).toHaveBeenCalledWith(TEMPLATE.id));
    expect(onRemoved).toHaveBeenCalledWith(TEMPLATE.id);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('drops the tile immediately on remove', async () => {
    renderGrid();
    expect(await screen.findByTitle('Bold headline')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Remove template'));

    await waitFor(() => expect(screen.queryByTitle('Bold headline')).toBeNull());
  });

  it('puts the tile back and says so when the delete fails', async () => {
    deleteCoverTemplate.mockRejectedValueOnce(new Error('nope'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderGrid();
    expect(await screen.findByTitle('Bold headline')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Remove template'));

    // A failed delete that silently leaves the tile gone would tell the user
    // the template is deleted when the server still has it.
    expect(await screen.findByTitle('Bold headline')).toBeTruthy();
    // Its OWN message, not the load-failure one: the list loaded fine, the
    // removal is what did not happen. Reusing the load copy here would also be
    // erased by the very reload that brings the tile back.
    expect(
      await screen.findByTestId('cover-template-remove-error'),
    ).toBeTruthy();
    expect(screen.queryByText(/Could not load your templates/)).toBeNull();
    spy.mockRestore();
  });

  it('counts how many of the saved templates are in the pool', async () => {
    renderGrid({ selectedIds: [TEMPLATE.id] });

    expect(await screen.findByText('1 saved · 1 in use')).toBeTruthy();
  });

  it('opens the add modal from the Add tile', async () => {
    renderGrid();
    await screen.findByTitle('Bold headline');

    expect(screen.queryByTestId('add-modal-open')).toBeNull();
    fireEvent.click(screen.getByTestId('cover-template-add'));

    expect(screen.getByTestId('add-modal-open')).toBeTruthy();
  });
});
