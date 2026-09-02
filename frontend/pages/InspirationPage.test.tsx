import { beforeEach, describe, expect, it, vi } from 'vitest';
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
const getHotspots = vi.fn();
const setHotspotState = vi.fn();
const getHotspot = vi.fn();
vi.mock('../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  setHotspotState: (...a: unknown[]) => setHotspotState(...a),
  getHotspot: (...a: unknown[]) => getHotspot(...a),
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
// Both the composer and the edit modal mount TipTap-backed NoteEditor; swap
// in the shared textarea shim so textbox queries in the edit-modal test
// below keep working without driving real contenteditable DOM (see
// InspirationPage.hotspots.test.tsx for the same pattern).
vi.mock('../components/Inspiration/NoteEditor', () => import('../components/Inspiration/testing/noteEditorShim'));

import { InspirationPage } from './InspirationPage';

// Inline tag chips (see components/Inspiration/NoteMarkdown.tsx) split a note
// body like "first idea #hooks" into a leading text node plus a
// `<button>#hooks</button>` chip, so `getByText('first idea #hooks')` no
// longer has a single text-only match. Match on the rendered `<p>`'s full
// (descendant-inclusive) `textContent` instead — mirrors
// NoteMarkdown.test.tsx's own `container.querySelector('p')?.textContent`
// idiom. Scoped to the `<p>` tag specifically (not "any element whose
// textContent equals X") because the wrapping NoteMarkdown/NoteCard divs
// around a single-paragraph body also have that same full textContent —
// matching any element would find those ancestors too and throw "multiple
// elements found".
const noteBody = (text: string) => (_content: string, el: Element | null) =>
  el?.tagName === 'P' && el.textContent === text;

const NOTE = {
  id: '1', content_md: 'first idea #hooks', tags: ['hooks'], ref_hotspot: null,
  pinned: false, rating: 0, note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00', updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
};

describe('InspirationPage', () => {
  beforeEach(() => {
    listNotes.mockResolvedValue([NOTE]);
    getTagCounts.mockResolvedValue([{ tag: 'hooks', cnt: 3 }]);
    getActivity.mockResolvedValue([]);
    getHotspots.mockReset();
    getHotspots.mockResolvedValue([]);
    setHotspotState.mockReset();
    getHotspot.mockReset();
    // HotspotDetail (nested under HotspotsWorkspace) fetches the full
    // hotspot for whichever row is selected; stub it so selecting a row
    // doesn't throw on an unmocked call.
    getHotspot.mockImplementation((id: string) => Promise.resolve({ id, title: 'stub', tags: [] }));
  });

  it('loads and renders notes grouped by day', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await waitFor(() => expect(listNotes).toHaveBeenCalled());
    expect(await screen.findByText(noteBody('first idea #hooks'))).toBeTruthy();
  });

  it('tag panel click sets filter chip and refetches with tag', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
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
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
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
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    fireEvent.click(screen.getByLabelText('Note actions'));
    fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => expect(deleteNote).toHaveBeenCalledWith('1'));
    await waitFor(() => expect(screen.queryByText(noteBody('first idea #hooks'))).toBeNull());
  });

  it('stale loadMore response is discarded after filters change', async () => {
    const first = Array.from({ length: 50 }, (_, i) => ({ ...NOTE, id: String(100 - i) }));
    listNotes.mockResolvedValueOnce(first); // initial page (hasMore=true, len===PAGE_SIZE)
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findAllByText(noteBody('first idea #hooks'));

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

  it('clicking a category chip filters the workspace list', async () => {
    getHotspots.mockResolvedValue([
      { id: '1', title: 'Food topic', tags: [], category: 'food', heat: 90 },
      { id: '2', title: 'Music topic', tags: [], category: 'music', heat: 80 },
    ]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    fireEvent.click(screen.getByText('Hotspots'));
    await waitFor(() => expect(screen.getByRole('button', { name: /Music topic/ })).toBeTruthy());
    fireEvent.click(screen.getByText('#food (1)'));
    await waitFor(() => expect(screen.queryByRole('button', { name: /Music topic/ })).toBeNull());
    expect(screen.getByRole('button', { name: /Food topic/ })).toBeTruthy();
  });

  it('edits a note through the modal (NoteEditor) and saves the new content', async () => {
    updateNote.mockResolvedValue({ ...NOTE, content_md: 'updated content #hooks' });
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    fireEvent.click(screen.getByLabelText('Note actions'));
    fireEvent.click(screen.getByText('Edit'));

    // The composer's NoteEditor is always mounted too, so disambiguate by
    // taking the last-rendered instance (the modal one).
    const editors = await screen.findAllByLabelText('note-editor');
    const modalEditor = editors[editors.length - 1] as HTMLTextAreaElement;
    expect(modalEditor.value).toBe('first idea #hooks');

    fireEvent.change(modalEditor, { target: { value: 'updated content #hooks' } });
    // The composer's own submit button is also labeled "Save" — the modal's
    // is the last one rendered (it mounts after the always-present composer).
    const saveButtons = screen.getAllByText('Save');
    fireEvent.click(saveButtons[saveButtons.length - 1]);

    await waitFor(() =>
      expect(updateNote).toHaveBeenCalledWith('1', { content_md: 'updated content #hooks' }),
    );
    await waitFor(() => expect(screen.getByText(noteBody('updated content #hooks'))).toBeTruthy());
    // Modal closes after a successful save.
    expect(screen.queryByText('Edit note')).toBeNull();
  });

  it('clicking the same category chip twice clears the filter', async () => {
    getHotspots.mockResolvedValue([
      { id: '1', title: 'Food topic', tags: [], category: 'food', heat: 90 },
      { id: '2', title: 'Music topic', tags: [], category: 'music', heat: 80 },
    ]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    fireEvent.click(screen.getByText('Hotspots'));
    await waitFor(() => expect(screen.getByRole('button', { name: /Music topic/ })).toBeTruthy());
    fireEvent.click(screen.getByText('#food (1)'));
    await waitFor(() => expect(screen.queryByRole('button', { name: /Music topic/ })).toBeNull());
    fireEvent.click(screen.getByText('#food (1)'));
    await waitFor(() => expect(screen.getByRole('button', { name: /Music topic/ })).toBeTruthy());
  });
});
