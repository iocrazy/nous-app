/**
 * The Inspiration filters live on their OWN row under the header, not inside
 * the header's right-aligned actions slot.
 *
 * WHAT BROKE: the tag pill, the date pill and the Rating chip were all passed
 * as `PageHeader`'s `actions`. That slot is `flex shrink-0 items-center` and
 * right-aligned — it exists for the page's verbs (search, Parse URL). Filters
 * dropped into it queued up on the same line and pushed left of the search
 * box, so the Rating chip read as a stray control floating mid-header. The
 * Resources bar has always done this the other way: its chips are a
 * `flex items-center gap-1.5 flex-wrap` row of their own beneath the search
 * line (`resources/filter/FilterBar.tsx`), which is also what lets them wrap
 * instead of squeezing the actions.
 *
 * Asserted structurally rather than visually — jsdom has no layout, so "looks
 * cramped" is not observable. What IS observable, and is exactly the thing
 * that was wrong, is WHERE in the tree the chips are mounted.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

// Real wire shape (NoteOut): id is a string, rating a number.
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

async function renderPage() {
  listNotes.mockResolvedValue([NOTE]);
  getTagCounts.mockResolvedValue([{ tag: 'hooks', cnt: 3 }]);
  getActivity.mockResolvedValue([]);
  const view = render(
    <MemoryRouter>
      <InspirationPage />
    </MemoryRouter>,
  );
  await screen.findByText('an idea');
  return view;
}

const ratingChip = (container: HTMLElement) =>
  container.querySelector('[data-chip-id="rating"]') as HTMLElement;

describe('Inspiration filter row', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('mounts the Rating chip outside the header actions slot', async () => {
    const { container } = await renderPage();

    // `actions` renders its children into one flex container; the Parse URL
    // button is the slot's anchor because it stays there.
    const actionsSlot = screen.getByRole('button', { name: 'Parse URL' })
      .parentElement as HTMLElement;

    expect(actionsSlot.contains(ratingChip(container))).toBe(false);
  });

  it('places the filter row after the header, not inside it', async () => {
    const { container } = await renderPage();

    const parseBtn = screen.getByRole('button', { name: 'Parse URL' });
    // DOCUMENT_POSITION_FOLLOWING === the chip comes later in the document
    // than the header's last action. Before the fix the chip preceded it.
    expect(
      parseBtn.compareDocumentPosition(ratingChip(container)) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('keeps every filter control on that one row', async () => {
    const { container } = await renderPage();
    await waitFor(() => expect(getTagCounts).toHaveBeenCalled());

    // The row is shared, not one row per chip — that is what makes it wrap as
    // a unit when the window narrows.
    const tagsChip = container.querySelector(
      '[data-chip-id="note_tags"]',
    ) as HTMLElement;
    expect(tagsChip.parentElement).toBe(ratingChip(container).parentElement);
  });
});
