import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listNotes = vi.fn();
const getTagCounts = vi.fn();
const getActivity = vi.fn();
const deleteNote = vi.fn();
const updateNote = vi.fn();
const deleteAttachment = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: (...a: unknown[]) => getTagCounts(...a),
  getActivity: (...a: unknown[]) => getActivity(...a),
  deleteNote: (...a: unknown[]) => deleteNote(...a),
  updateNote: (...a: unknown[]) => updateNote(...a),
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
  deleteAttachment: (...a: unknown[]) => deleteAttachment(...a),
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
    // Reset the write-path spies too: several tests below assert "was not
    // called", which a leaked call from an earlier test silently defeats.
    listNotes.mockReset();
    updateNote.mockReset();
    deleteNote.mockReset();
    deleteAttachment.mockReset();
    addToast.mockReset();
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
    // The active tag now shows on the Tags chip; the "#tag ×" pill it used to
    // put in the header was retired when that chip landed (it could only
    // clear a tag, never pick one, so the two were the same state twice).
    expect(screen.getByRole('button', { name: /^Tags · #hooks/ })).toBeTruthy();
    expect(screen.getByLabelText('Clear Tags')).toBeTruthy();
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

  // ── rating filter ──────────────────────────────────────────────────────
  // Opens the chip (if shut) and picks an option out of RatingFilterDropdown's
  // menu BY NAME — the "≥N" options carry an aria-label for exactly this, so a
  // reordered CHOICES list fails with "unable to find ≥4" instead of silently
  // clicking a different star count.
  // The chip's accessible name gains a " · ≥N★" summary once active, and
  // picking a floor deliberately leaves the dropdown open (only "Any" closes
  // it) — hence the prefix match and the open-state check.
  const pickRating = async (name: string) => {
    if (!screen.queryByRole('menu', { name: 'Rating filter' })) {
      fireEvent.click(screen.getByRole('button', { name: /^Rating/ }));
    }
    const menu = await screen.findByRole('menu', { name: 'Rating filter' });
    fireEvent.click(within(menu).getByRole('button', { name }));
  };

  it('picking a rating floor refetches with min_rating', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await waitFor(() => expect(listNotes).toHaveBeenCalled());
    await pickRating('≥4');
    await waitFor(() =>
      expect(listNotes).toHaveBeenLastCalledWith(
        expect.objectContaining({ min_rating: 4 }),
        expect.anything(),
        undefined,
      ),
    );
    expect(screen.getByLabelText('Clear Rating')).toBeTruthy();
  });

  // "Any rating" must mean *no filter*, not `min_rating: 0` — the two are
  // indistinguishable on screen but only the former is the right request.
  it('choosing Any rating clears min_rating rather than sending 0', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await waitFor(() => expect(listNotes).toHaveBeenCalled());
    await pickRating('≥4');
    await waitFor(() =>
      expect(listNotes).toHaveBeenLastCalledWith(
        expect.objectContaining({ min_rating: 4 }),
        expect.anything(),
        undefined,
      ),
    );
    await pickRating('Any rating');
    await waitFor(() => expect(listNotes.mock.lastCall?.[0].min_rating).toBeUndefined());
    expect(screen.queryByLabelText('Clear Rating')).toBeNull();
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

  // The edit modal is now the Composer itself (same editor, toolbar, staged
  // files and attachment handling as quick capture), so it is scoped by its
  // dialog role rather than by "take the last rendered textbox / Save" —
  // that positional disambiguation broke the moment the modal grew a second
  // button, and would have silently pointed at the wrong control instead of
  // failing loudly.
  const openEditModal = async () => {
    fireEvent.click(screen.getByLabelText('Note actions'));
    fireEvent.click(screen.getByText('Edit'));
    return screen.findByRole('dialog', { name: 'Edit note' });
  };

  it('edits a note through the modal and saves the new content', async () => {
    updateNote.mockResolvedValue({ ...NOTE, content_md: 'updated content #hooks' });
    // A successful save bumps refreshKey, so listNotes runs again — model the
    // server rather than letting the assertion race that refetch: before the
    // fix this test only passed when it happened to check first.
    listNotes.mockResolvedValueOnce([NOTE]);
    listNotes.mockResolvedValue([{ ...NOTE, content_md: 'updated content #hooks' }]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();

    const editor = within(modal).getByRole('textbox') as HTMLTextAreaElement;
    expect(editor.value).toBe('first idea #hooks');
    fireEvent.change(editor, { target: { value: 'updated content #hooks' } });
    fireEvent.click(within(modal).getByText('Save Changes'));

    await waitFor(() =>
      expect(updateNote).toHaveBeenCalledWith('1', { content_md: 'updated content #hooks' }),
    );
    await waitFor(() => expect(screen.getByText(noteBody('updated content #hooks'))).toBeTruthy());
    // Modal closes after a successful save.
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a failed save keeps the modal open and reports why', async () => {
    updateNote.mockRejectedValue(new Error('note update failed'));
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();
    fireEvent.change(within(modal).getByRole('textbox'), { target: { value: 'broken edit' } });
    fireEvent.click(within(modal).getByText('Save Changes'));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('note update failed', 'error'));
    // Still open, still holding the user's text — the edit is not lost.
    expect(screen.getByRole('dialog', { name: 'Edit note' })).toBeTruthy();
    expect((within(modal).getByRole('textbox') as HTMLTextAreaElement).value).toBe('broken edit');
  });

  it('cancelling closes the modal without writing anything', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();
    fireEvent.change(within(modal).getByRole('textbox'), { target: { value: 'discarded' } });
    fireEvent.click(within(modal).getByLabelText('Cancel edit'));

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(updateNote).not.toHaveBeenCalled();
    expect(screen.getByText(noteBody('first idea #hooks'))).toBeTruthy();
  });

  it('the modal removes a stored attachment and drops it from the card', async () => {
    const withFile = {
      ...NOTE,
      attachments: [{ id: 'a2', mime: 'application/pdf', size_bytes: 20, original_name: 'report.pdf' }],
    };
    // Real wire shapes: after the DELETE lands, neither the PATCH response
    // nor a refetch still carries that attachment. Mocking them as if they
    // did would test a server that does not exist.
    listNotes.mockResolvedValueOnce([withFile]);
    listNotes.mockResolvedValue([{ ...withFile, attachments: [] }]);
    deleteAttachment.mockResolvedValue(undefined);
    updateNote.mockResolvedValue({ ...withFile, attachments: [] });
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    // The card renders the file first; the modal then offers to remove it.
    await waitFor(() => expect(screen.getAllByText('report.pdf').length).toBe(1));
    const modal = await openEditModal();

    fireEvent.click(within(modal).getByLabelText('Remove report.pdf'));
    fireEvent.click(within(modal).getByText('Save Changes'));

    await waitFor(() => expect(deleteAttachment).toHaveBeenCalledWith('a2'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // The card must not keep showing a file that is gone from the server.
    expect(screen.queryByText('report.pdf')).toBeNull();
  });

  it('a removal that landed leaves the card even when the same save then fails', async () => {
    const withFile = {
      ...NOTE,
      attachments: [{ id: 'a2', mime: 'application/pdf', size_bytes: 20, original_name: 'report.pdf' }],
    };
    listNotes.mockResolvedValue([withFile]);
    deleteAttachment.mockResolvedValue(undefined);
    updateNote.mockRejectedValue(new Error('note update failed'));
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getAllByText('report.pdf').length).toBe(1));
    const modal = await openEditModal();

    fireEvent.click(within(modal).getByLabelText('Remove report.pdf'));
    fireEvent.click(within(modal).getByText('Save Changes'));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('note update failed', 'error'));
    // The DELETE already landed, so the file is gone server-side. Without the
    // onAttachmentDeleted wiring the card would keep advertising a file that
    // no longer exists — and nothing would ever correct it, because the modal
    // is still open and the PATCH that would refresh the list never succeeded.
    expect(screen.queryByText('report.pdf')).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Edit note' })).toBeTruthy();
  });

  it('scrolls its own body instead of pushing Save out of the viewport', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();
    // jsdom has no layout, so this is a structural pin rather than a real
    // overflow measurement: the modal sits in a `fixed inset-0` centering
    // container that does NOT scroll, and attachments (video is max-h-64)
    // now live inside it — without these two classes a note with a couple of
    // videos puts Save and the lower attachments' remove buttons off-screen
    // with no way to reach them by mouse.
    expect(modal.className).toContain('max-h-[85vh]');
    expect(modal.className).toContain('overflow-y-auto');
  });

  it('Escape closes the edit modal without saving', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();
    fireEvent.change(within(modal).getByRole('textbox'), { target: { value: 'discarded' } });

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(updateNote).not.toHaveBeenCalled();
  });

  it('Escape with the image lightbox open closes only the lightbox', async () => {
    const withImage = {
      ...NOTE,
      attachments: [{ id: 'a1', mime: 'image/png', size_bytes: 10, original_name: 'pic.png' }],
    };
    listNotes.mockResolvedValue([withImage]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();

    // Both the modal and the lightbox listen for Escape on window, and the
    // lightbox (z-100) stacks above the modal (z-50). One press must not
    // collapse both layers.
    fireEvent.click(within(modal).getByLabelText('View pic.png'));
    expect(await screen.findByRole('dialog', { name: 'pic.png' })).toBeTruthy();

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'pic.png' })).toBeNull());
    expect(screen.getByRole('dialog', { name: 'Edit note' })).toBeTruthy();

    // The next press, with nothing above it, does close the modal.
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('moves focus into the modal on open and returns it on close', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const trigger = screen.getByLabelText('Note actions');
    // NOT calling trigger.focus() here on purpose. jsdom's fireEvent.click
    // does not move focus, so hand-placing it would build a timeline that
    // never happens in a browser: there, mousedown focuses the "Edit" menu
    // ITEM, the same click unmounts the menu, and activeElement resets to
    // <body> — which this effect would then dutifully "restore" to, a no-op.
    // Walking the real path is what makes the restore assertion falsifiable.
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText('Edit'));

    const modal = await screen.findByRole('dialog', { name: 'Edit note' });
    // aria-modal="true" tells assistive tech the rest of the page is inert.
    // Leaving focus behind on the page makes that a false claim.
    expect(modal.contains(document.activeElement)).toBe(true);

    fireEvent.click(within(modal).getByLabelText('Cancel edit'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it('pulls Tab back inside the modal instead of letting it reach the page behind', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();

    // Park focus on something behind the modal, then Tab.
    const outside = screen.getByLabelText('Note actions');
    outside.focus();
    fireEvent.keyDown(window, { key: 'Tab' });

    expect(modal.contains(document.activeElement)).toBe(true);
  });

  it('leaves Tab alone while the lightbox is on top', async () => {
    const withImage = {
      ...NOTE,
      attachments: [{ id: 'a1', mime: 'image/png', size_bytes: 10, original_name: 'pic.png' }],
    };
    listNotes.mockResolvedValue([withImage]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();
    fireEvent.click(within(modal).getByLabelText('View pic.png'));
    await screen.findByRole('dialog', { name: 'pic.png' });

    const outside = screen.getByLabelText('Note actions');
    outside.focus();
    fireEvent.keyDown(window, { key: 'Tab' });

    // The lightbox is the modal layer right now. Yanking focus down into the
    // editor underneath it is the same bug as closing the wrong layer on
    // Escape — the guard has to cover BOTH keys, not just Escape.
    expect(document.activeElement).toBe(outside);
  });

  it('Shift+Tab from the freshly-opened dialog wraps to the last control', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();
    // Focus starts on the dialog container itself (tabIndex={-1}), which is
    // neither "outside" nor the first focusable — the gap Shift+Tab escaped
    // through before.
    expect(document.activeElement).toBe(modal);

    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });

    expect(document.activeElement).toBe(within(modal).getByText('Save Changes'));
  });

  it('Tab from the last control wraps to the first', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await screen.findByText(noteBody('first idea #hooks'));
    const modal = await openEditModal();
    (within(modal).getByText('Save Changes') as HTMLElement).focus();

    fireEvent.keyDown(window, { key: 'Tab' });

    expect(document.activeElement).toBe(within(modal).getByLabelText('Cancel edit'));
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
