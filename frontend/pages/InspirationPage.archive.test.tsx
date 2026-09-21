/**
 * Archiving a note (mig 478) — the third state between pinning and deleting.
 *
 * What makes this worth pinning at the page level: archive is not a property
 * of a note, it is which LIST the note is in. So the things that can break are
 * all seams between components — the segmented control and the fetch, the
 * menu item and the view it was opened from, the write and the row that has to
 * leave the screen. Each of those is a silent failure: the note simply stays
 * where it was, or the wrong list comes back, and nothing errors.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listNotes = vi.fn();
const updateNote = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: vi.fn().mockResolvedValue([]),
  getActivity: vi.fn().mockResolvedValue([]),
  deleteNote: vi.fn(),
  updateNote: (...a: unknown[]) => updateNote(...a),
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
  deleteAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../services/topicService', () => ({
  getHotspots: vi.fn().mockResolvedValue([]),
  setHotspotState: vi.fn(),
  getHotspot: vi.fn(),
}));
vi.mock('../services/unifiedTagService', () => ({ fetchAllTags: vi.fn().mockResolvedValue([]) }));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));
vi.mock('../components/AILibrary/MarkdownBody', () => ({
  default: ({ source }: { source: string }) => <div>{source}</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

const addToast = vi.fn();
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast }) }));
vi.mock('../components/ConfirmDialog', () => ({ useConfirm: () => vi.fn() }));
vi.mock('../components/Inspiration/NoteEditor', () => import('../components/Inspiration/testing/noteEditorShim'));

import { InspirationPage } from './InspirationPage';

const LIVE = {
  id: '1', content_md: 'a live idea', tags: [], ref_hotspot: null,
  pinned: false, rating: 0, note_date: '2026-09-20',
  created_at: '2026-09-20T09:42:00+00:00', updated_at: '2026-09-20T09:42:00+00:00',
  archived_at: null, attachments: [],
};
const ARCHIVED = {
  ...LIVE, id: '2', content_md: 'a put-away idea',
  archived_at: '2026-09-19T10:00:00+00:00',
};

/** The `archived` flag of the most recent listNotes call. */
const lastAskedFor = () =>
  (listNotes.mock.calls.at(-1)?.[0] as { archived?: boolean } | undefined)?.archived;

async function mount() {
  render(
    <MemoryRouter>
      <InspirationPage />
    </MemoryRouter>,
  );
  await screen.findByText('a live idea');
}

async function openMenu() {
  fireEvent.click(await screen.findByRole('button', { name: 'Note actions' }));
  return within(screen.getByRole('menu', { name: 'Note actions' }));
}

/** The Active ｜ Archived switch — named so it is never confused with the
 *  "Notes" tab above it or with a card's own menu items. */
const viewSwitch = () =>
  within(screen.getByRole('group', { name: 'Note view' }));

const showArchive = () =>
  fireEvent.click(viewSwitch().getByRole('button', { name: 'Archived' }));

describe('InspirationPage — archive', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listNotes.mockImplementation(async (f: { archived?: boolean }) =>
      f?.archived ? [ARCHIVED] : [LIVE],
    );
  });

  it('opens on the live list, and does not ask the backend for the archive', async () => {
    await mount();
    expect(lastAskedFor()).toBe(false);
  });

  it('the Archive switch refetches the other view', async () => {
    await mount();
    showArchive();
    await screen.findByText('a put-away idea');
    expect(lastAskedFor()).toBe(true);
    // The live note is not merely hidden — the two lists are disjoint.
    expect(screen.queryByText('a live idea')).toBeNull();
  });

  it('hides the composer in the archive', async () => {
    // Anything written there would be created LIVE and vanish from the view it
    // was typed into.
    await mount();
    const composer = () => screen.queryByPlaceholderText(/^Capture an idea/);
    expect(composer()).not.toBeNull();
    showArchive();
    await screen.findByText('a put-away idea');
    expect(composer()).toBeNull();
  });

  it('archives a note and takes it off the list', async () => {
    updateNote.mockResolvedValue({ ...LIVE, archived_at: '2026-09-20T10:00:00+00:00' });
    await mount();
    const menu = await openMenu();
    fireEvent.click(menu.getByRole('button', { name: 'Archive' }));

    await waitFor(() => expect(updateNote).toHaveBeenCalledWith('1', { archived: true }));
    await waitFor(() => expect(screen.queryByText('a live idea')).toBeNull());
  });

  it('offers Undo on the toast, which puts the note back', async () => {
    // No confirm dialog up front: archiving is reversible, so the offer rides
    // the toast that reports it rather than a question before it.
    updateNote.mockResolvedValue({ ...LIVE, archived_at: '2026-09-20T10:00:00+00:00' });
    await mount();
    const menu = await openMenu();
    fireEvent.click(menu.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(addToast).toHaveBeenCalled());

    const [, , , action] = addToast.mock.calls.at(-1) as [
      string, string, number, { label: string; onClick: () => void },
    ];
    expect(action.label).toBe('Undo');

    updateNote.mockResolvedValue(LIVE);
    action.onClick();
    await waitFor(() => expect(updateNote).toHaveBeenLastCalledWith('1', { archived: false }));
    await screen.findByText('a live idea');
  });

  it('keeps the note on screen when the archive write fails', async () => {
    // A failed write must be visible and leave the list telling the truth —
    // never a silent no-op that hides a note the server still has.
    updateNote.mockRejectedValue(new Error('archive failed'));
    await mount();
    const menu = await openMenu();
    fireEvent.click(menu.getByRole('button', { name: 'Archive' }));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('archive failed', 'error'));
    expect(screen.getByText('a live idea')).toBeTruthy();
  });

  it('an archived note is offered Unarchive, and no Pin', async () => {
    await mount();
    showArchive();
    await screen.findByText('a put-away idea');
    const menu = await openMenu();

    expect(menu.getByRole('button', { name: 'Unarchive' })).toBeTruthy();
    // A pin is a claim on the top of the live list, which this note is not in.
    expect(menu.queryByRole('button', { name: 'Pin' })).toBeNull();
  });
});
