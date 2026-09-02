// Regression test for the checkbox-toggle out-of-order-response race (P4a
// Task 3 review finding, "Important"): rapid-fire clicks on two checkboxes
// in the same note fire two concurrent `updateNote` PATCHes, and network
// responses are not guaranteed to land in request order. If the *older*
// request's response arrives after the *newer* one, applying it verbatim
// would silently overwrite the newer optimistic/applied state with content
// that only reflects the older (now-superseded) toggle — a checked box
// "un-checking itself" until the next reload. The fix is a per-note
// in-flight write sequence counter (mirrors the `requestSeq` pattern this
// file already uses for loadMore/filter races) that discards any response
// that isn't from the latest write for that note.
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const NOTE = {
  id: '1',
  content_md: '- [ ] a\n- [ ] b',
  tags: [],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
};

/** A promise plus externally-callable resolve/reject, for controlling the
 *  order in which concurrent `updateNote` calls settle. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe('InspirationPage checkbox toggle race', () => {
  it('discards a stale updateNote response that arrives after a newer toggle already resolved', async () => {
    listNotes.mockResolvedValue([NOTE]);
    getTagCounts.mockResolvedValue([]);
    getActivity.mockResolvedValue([]);

    const first = deferred<typeof NOTE>(); // older write: toggling checkbox 0
    const second = deferred<typeof NOTE>(); // newer write: toggling checkbox 1
    updateNote.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText('a');

    const boxes = () => screen.getAllByRole('checkbox') as HTMLInputElement[];
    expect(boxes()).toHaveLength(2);

    // Click checkbox 0 (older write, seq=1), then checkbox 1 (newer write,
    // seq=2) — both PATCHes are now in flight concurrently.
    fireEvent.click(boxes()[0]);
    await waitFor(() => expect(updateNote).toHaveBeenCalledTimes(1));
    fireEvent.click(boxes()[1]);
    await waitFor(() => expect(updateNote).toHaveBeenCalledTimes(2));

    // Network resolves out of order: the newer (seq=2) response lands
    // first, reflecting both boxes checked (the true current state).
    await act(async () => {
      second.resolve({ ...NOTE, content_md: '- [x] a\n- [x] b' });
      await Promise.resolve();
    });

    // The older (seq=1) response lands late, reflecting only the first
    // toggle — a stale, now-superseded server view. It must be discarded,
    // not applied over the newer state.
    await act(async () => {
      first.resolve({ ...NOTE, content_md: '- [x] a\n- [ ] b' });
      await Promise.resolve();
    });

    await waitFor(() => {
      const final = boxes();
      expect(final[0].checked).toBe(true);
      expect(final[1].checked).toBe(true);
    });
  });
});
