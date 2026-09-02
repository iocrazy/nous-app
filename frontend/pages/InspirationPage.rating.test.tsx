// Page-level regression cover for the star-rating write path (mig 448 review
// finding, "Important"). `onRating` is a line-for-line clone of the
// `onToggleTask` seq-guard that InspirationPage.toggletask.test.tsx exists to
// protect — same optimistic update, same per-note in-flight sequence counter,
// same rollback — but it shipped with no page-level test at all: the ordering
// guard, the failure rollback, and the whole
// RatingStars -> NoteCard -> NoteTimeline -> page props chain were unpinned.
// This file mirrors that test file deliberately; keep the two in step.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listNotes = vi.fn();
const getTagCounts = vi.fn();
const getActivity = vi.fn();
const deleteNote = vi.fn();
const updateNote = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: (...a: unknown[]) => getTagCounts(...a),
  getActivity: (...a: unknown[]) => getActivity(...a),
  deleteNote: (...a: unknown[]) => deleteNote(...a),
  updateNote: (...a: unknown[]) => updateNote(...a),
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: 'tok' }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
const addToast = vi.fn();
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast }) }));

import { InspirationPage } from './InspirationPage';

// Real wire shape: NoteOut gives id as a string, rating as a number.
const NOTE = {
  id: '1',
  content_md: 'an idea',
  tags: [],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const stars = () => within(screen.getByLabelText('Rating')).getAllByRole('button');

/** How many stars render as filled. RatingStars marks the filled ones with
 *  `fill-amber-400` on the icon, so this reads the actual displayed rating
 *  rather than trusting the value we passed in. */
const filledCount = () =>
  stars().filter((b) => b.querySelector('svg')?.classList.contains('fill-amber-400')).length;

async function renderWithNote(note = NOTE) {
  listNotes.mockResolvedValue([note]);
  getTagCounts.mockResolvedValue([]);
  getActivity.mockResolvedValue([]);
  render(<MemoryRouter><InspirationPage /></MemoryRouter>);
  await screen.findByText('an idea');
}

describe('InspirationPage star rating', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('sends the clicked star as a rating PATCH and shows it optimistically', async () => {
    await renderWithNote();
    updateNote.mockResolvedValue({ ...NOTE, rating: 4 });

    fireEvent.click(stars()[3]); // 4th star -> rating 4

    await waitFor(() => expect(updateNote).toHaveBeenCalledWith('1', { rating: 4 }));
    await waitFor(() => expect(filledCount()).toBe(4));
  });

  it('discards a stale updateNote response that arrives after a newer rating already resolved', async () => {
    await renderWithNote();

    const first = deferred<typeof NOTE>(); // older write: rating 2
    const second = deferred<typeof NOTE>(); // newer write: rating 5
    updateNote.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    fireEvent.click(stars()[1]); // rating 2, seq=1
    await waitFor(() => expect(updateNote).toHaveBeenCalledTimes(1));
    fireEvent.click(stars()[4]); // rating 5, seq=2
    await waitFor(() => expect(updateNote).toHaveBeenCalledTimes(2));

    // Out-of-order network: the newer (seq=2) response lands first.
    await act(async () => {
      second.resolve({ ...NOTE, rating: 5 });
      await Promise.resolve();
    });
    // The older (seq=1) response lands late with a now-superseded server view.
    // Applying it would drop the card back to 2 stars until the next reload.
    await act(async () => {
      first.resolve({ ...NOTE, rating: 2 });
      await Promise.resolve();
    });

    await waitFor(() => expect(filledCount()).toBe(5));
  });

  it('rolls the stars back and toasts when the PATCH fails', async () => {
    await renderWithNote({ ...NOTE, rating: 3 });
    updateNote.mockRejectedValue(new Error('boom'));

    expect(filledCount()).toBe(3);
    fireEvent.click(stars()[4]); // optimistically 5

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('boom', 'error'));
    await waitFor(() => expect(filledCount()).toBe(3));
  });
});
