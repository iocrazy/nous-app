import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

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
// NoteCard imports MarkdownBody as a default export whose real prop is
// `source` (not `content` — see components/AILibrary/MarkdownBody.tsx).
vi.mock('../components/AILibrary/MarkdownBody', () => ({
  default: ({ source }: { source: string }) => <div>{source}</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
  // NoteTimeline pulls in utils/formatDate.ts -> i18n.ts, which calls
  // `i18n.use(initReactI18next)` at module-eval time — the named export
  // must exist on the mock or vitest throws before any test runs.
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
const addToast = vi.fn();
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast }) }));

import { InspirationPage } from './InspirationPage';

const NOTE = {
  id: '1', content_md: 'first idea #hooks', tags: ['hooks'], ref_hotspot: null,
  pinned: false, note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00', updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
};

describe('InspirationPage', () => {
  beforeEach(() => {
    listNotes.mockResolvedValue([NOTE]);
    getTagCounts.mockResolvedValue([{ tag: 'hooks', cnt: 3 }]);
    getActivity.mockResolvedValue([]);
  });

  it('loads and renders notes grouped by day', async () => {
    render(<InspirationPage />);
    await waitFor(() => expect(listNotes).toHaveBeenCalled());
    expect(await screen.findByText('first idea #hooks')).toBeTruthy();
  });

  it('tag panel click sets filter chip and refetches with tag', async () => {
    render(<InspirationPage />);
    await waitFor(() => expect(getTagCounts).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('#hooks (3)'));
    await waitFor(() =>
      expect(listNotes).toHaveBeenLastCalledWith(
        expect.objectContaining({ tag: 'hooks' }),
        expect.anything(),
        undefined,
      ),
    );
    expect(screen.getByLabelText('Clear tag filter')).toBeTruthy();
  });

  it('search input debounces into q filter', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<InspirationPage />);
    await waitFor(() => expect(listNotes).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText('Search notes…'), { target: { value: 'ferry' } });
    await vi.advanceTimersByTimeAsync(350);
    await waitFor(() =>
      expect(listNotes).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'ferry' }),
        expect.anything(),
        undefined,
      ),
    );
    vi.useRealTimers();
  });

  it('delete flows through confirm and removes the card', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    deleteNote.mockResolvedValue(undefined);
    render(<InspirationPage />);
    await screen.findByText('first idea #hooks');
    fireEvent.click(screen.getByLabelText('Note actions'));
    fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => expect(deleteNote).toHaveBeenCalledWith('1'));
    await waitFor(() => expect(screen.queryByText('first idea #hooks')).toBeNull());
  });

  it('stale loadMore response is discarded after filters change', async () => {
    const first = Array.from({ length: 50 }, (_, i) => ({ ...NOTE, id: String(100 - i) }));
    listNotes.mockResolvedValueOnce(first); // initial page (hasMore=true, len===PAGE_SIZE)
    render(<InspirationPage />);
    await screen.findAllByText('first idea #hooks');

    // Reset the call counter so earlier tests' invocation history doesn't
    // pollute the relative assertions below (mocks aren't reset between
    // `it` blocks in this file).
    listNotes.mockClear();

    let resolveStale: (v: unknown) => void = () => {};
    const stale = new Promise((r) => {
      resolveStale = r;
    });
    listNotes.mockReturnValueOnce(stale as Promise<never>); // loadMore hangs
    fireEvent.click(screen.getByText('Load more'));

    listNotes.mockResolvedValueOnce([NOTE]); // refetch triggered by tag filter change
    fireEvent.click(await screen.findByText('#hooks (3)'));
    await waitFor(() => expect(listNotes).toHaveBeenCalledTimes(2));

    // `waitFor` only polls until a condition becomes truthy — it is not a
    // reliable way to assert something *never* appears, since it can
    // succeed on its very first (synchronous) check before the stale
    // response's microtask chain has run. Flush explicitly via a macrotask
    // instead, then assert the settled DOM.
    await act(async () => {
      resolveStale([{ ...NOTE, id: '1', content_md: 'STALE ROW' }]);
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByText('STALE ROW')).toBeNull();
  });
});
