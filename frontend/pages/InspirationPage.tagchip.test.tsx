/**
 * The Inspiration filter row has a Tags chip.
 *
 * WHAT WAS MISSING: narrowing notes by tag was only reachable from the
 * sidebar TagsPanel, which is a frequency list rendered under the notes feed —
 * not where anyone looks for a filter, and gone entirely on the Hotspots tab.
 * The filter row carried Rating but no Tags.
 *
 * SINGLE-SELECT, on purpose: the backend takes one `tag` (a single optional
 * query param — `inspiration_router.py`, and `NoteFilters.tag?: string`), so a
 * multi-select picker would be a control that silently drops all but one of
 * the user's choices. Re-clicking the selected tag clears it, matching the
 * TagsPanel's own toggle.
 *
 * NOT `resources/filter/TagsFilterDropdown`: that one takes `Tag` entity
 * UUIDs and mounts a 520x360 EagleTagBrowser. Inspiration note tags are bare
 * strings in a `TEXT[]`, with counts — a different domain, not a skinnable
 * variant of the same one.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listNotes = vi.fn();
const getTagCounts = vi.fn();
const getActivity = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: (...a: unknown[]) => getTagCounts(...a),
  getActivity: (...a: unknown[]) => getActivity(...a),
  deleteNote: vi.fn(),
  updateNote: vi.fn(),
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
  deleteAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: 'tok' }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import { InspirationPage } from './InspirationPage';

// Real wire shape (NoteOut): id is a string, tags a string array.
const NOTE = {
  id: '1',
  content_md: 'an idea',
  tags: ['hooks'],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
};

// getTagCounts' real shape: `{ tag, cnt }` rows, cnt a number.
const TAG_COUNTS = [
  { tag: 'hooks', cnt: 3 },
  { tag: 'ferry', cnt: 1 },
];

async function renderPage() {
  listNotes.mockResolvedValue([NOTE]);
  getTagCounts.mockResolvedValue(TAG_COUNTS);
  getActivity.mockResolvedValue([]);
  render(
    <MemoryRouter>
      <InspirationPage />
    </MemoryRouter>,
  );
  await screen.findByText('an idea');
  await waitFor(() => expect(getTagCounts).toHaveBeenCalled());
}

/** Open the chip and hand back its menu. */
async function openTagsMenu() {
  fireEvent.click(screen.getByRole('button', { name: /^Tags/ }));
  return screen.findByRole('menu', { name: 'Note tags filter' });
}

const lastFilters = () => listNotes.mock.calls[listNotes.mock.calls.length - 1][0];

describe('Inspiration tags chip', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('lists every note tag with its count', async () => {
    await renderPage();
    const menu = await openTagsMenu();

    // Both the name and the count — the count is the whole reason to pick one
    // tag over another.
    expect(within(menu).getByRole('button', { name: /hooks/ }).textContent).toContain('3');
    expect(within(menu).getByRole('button', { name: /ferry/ }).textContent).toContain('1');
  });

  it('sends the picked tag as the single `tag` filter', async () => {
    await renderPage();
    const menu = await openTagsMenu();

    fireEvent.click(within(menu).getByRole('button', { name: /hooks/ }));

    await waitFor(() => expect(lastFilters()).toMatchObject({ tag: 'hooks' }));
  });

  it('replaces rather than accumulates when a second tag is picked', async () => {
    await renderPage();
    fireEvent.click(within(await openTagsMenu()).getByRole('button', { name: /hooks/ }));
    await waitFor(() => expect(lastFilters()).toMatchObject({ tag: 'hooks' }));

    fireEvent.click(within(await openTagsMenu()).getByRole('button', { name: /ferry/ }));

    // The backend takes ONE tag. Asserted as an exact value, not "contains
    // ferry" — an implementation that appended would still contain it.
    await waitFor(() => expect(lastFilters().tag).toBe('ferry'));
  });

  it('clears the filter when the selected tag is picked again', async () => {
    await renderPage();
    fireEvent.click(within(await openTagsMenu()).getByRole('button', { name: /hooks/ }));
    await waitFor(() => expect(lastFilters()).toMatchObject({ tag: 'hooks' }));

    fireEvent.click(within(await openTagsMenu()).getByRole('button', { name: /hooks/ }));

    // `undefined`, not `''` or `null`: listNotes drops the param on falsy, and
    // the page's `filters` memo is built with `tag ?? undefined`.
    await waitFor(() => expect(lastFilters().tag).toBeUndefined());
  });

  it('shows the active tag on the chip and clears it from there', async () => {
    await renderPage();
    fireEvent.click(within(await openTagsMenu()).getByRole('button', { name: /hooks/ }));
    await waitFor(() => expect(lastFilters()).toMatchObject({ tag: 'hooks' }));

    expect(screen.getByRole('button', { name: /^Tags · #hooks/ })).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Clear Tags'));
    await waitFor(() => expect(lastFilters().tag).toBeUndefined());
  });

  it('sits on the filter row next to the Rating chip', async () => {
    await renderPage();
    const container = document.body;

    const tagsChip = container.querySelector('[data-chip-id="note_tags"]') as HTMLElement;
    const rating = container.querySelector('[data-chip-id="rating"]') as HTMLElement;
    expect(tagsChip.parentElement).toBe(rating.parentElement);
  });
});
