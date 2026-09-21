/**
 * Switching Active ｜ Archived takes effect on the click, not on the response.
 *
 * Reported as "点击已归档，会有延迟，再显示". The request IS a real round trip
 * and that part cannot be removed — but almost none of what the user felt was
 * the network. `switchView` used to be a bare `setView`, so during the refetch
 * `notes` still held the PREVIOUS view's rows and NoteTimeline's empty guard
 * (`!notes.length && !loading`) left them fully rendered. The click looked
 * inert, then the content swapped wholesale.
 *
 * So every case here asserts on what is on screen WHILE the fetch is still in
 * flight. A test that awaited the response would pass against the old code
 * too — the end state was never wrong, only the beat before it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listNotes = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: vi.fn().mockResolvedValue([]),
  getActivity: vi.fn().mockResolvedValue([]),
  deleteNote: vi.fn(),
  updateNote: vi.fn(),
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
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../components/ConfirmDialog', () => ({ useConfirm: () => vi.fn() }));
vi.mock('../components/Inspiration/NoteEditor', () => import('../components/Inspiration/testing/noteEditorShim'));

import { InspirationPage } from './InspirationPage';

const note = (id: string, body: string, archived: string | null) => ({
  id, content_md: body, tags: [], ref_hotspot: null,
  pinned: false, rating: 0, note_date: '2026-09-20',
  created_at: '2026-09-20T09:42:00+00:00', updated_at: '2026-09-20T09:42:00+00:00',
  archived_at: archived, attachments: [],
});
const LIVE = note('1', 'a live idea', null);
const ARCHIVED = note('2', 'a put-away idea', '2026-09-19T10:00:00+00:00');

/** A fetch the test decides when to answer — the only way to look at the
 *  screen mid-flight. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

const viewSwitch = () => within(screen.getByRole('group', { name: 'Note view' }));
const clickView = (name: 'Active' | 'Archived') =>
  fireEvent.click(viewSwitch().getByRole('button', { name }));

/** Answer every fetch immediately, from the requested view. */
const answerImmediately = () =>
  listNotes.mockImplementation(async (f: { archived?: boolean }) =>
    f?.archived ? [ARCHIVED] : [LIVE],
  );

async function mount() {
  answerImmediately();
  render(<MemoryRouter><InspirationPage /></MemoryRouter>);
  await screen.findByText('a live idea');
}

describe('InspirationPage — switching views feels immediate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('drops the previous view the moment it is clicked', async () => {
    await mount();
    const pending = deferred<unknown[]>();
    listNotes.mockReturnValue(pending.promise);

    clickView('Archived');

    // Still in flight — and the live note must already be gone. Before the
    // fix it stayed here, which is the whole complaint.
    expect(listNotes).toHaveBeenCalled();
    expect(screen.queryByText('a live idea')).toBeNull();

    await act(async () => {
      pending.resolve([ARCHIVED]);
    });
    await screen.findByText('a put-away idea');
  });

  it('shows a skeleton during that beat instead of blank space', async () => {
    await mount();
    listNotes.mockReturnValue(deferred<unknown[]>().promise);

    clickView('Archived');

    expect(screen.getByTestId('note-timeline-skeleton')).toBeTruthy();
  });

  it('going back shows the previous rows without waiting for the network', async () => {
    await mount();
    clickView('Archived');
    await screen.findByText('a put-away idea');

    // Hold the refetch open: whatever is on screen now came from the snapshot,
    // not from this request.
    listNotes.mockReturnValue(deferred<unknown[]>().promise);
    clickView('Active');

    expect(screen.getByText('a live idea')).toBeTruthy();
    expect(screen.queryByText('a put-away idea')).toBeNull();
    expect(screen.queryByTestId('note-timeline-skeleton')).toBeNull();
  });

  it('the snapshot carries local edits, not the response it came from', async () => {
    // It is taken from state, so an optimistic change made in a view survives
    // a trip to the other one. A cache keyed off responses would lose it.
    await mount();
    listNotes.mockImplementation(async (f: { archived?: boolean }) =>
      f?.archived ? [ARCHIVED] : [note('1', 'edited in place', null)],
    );

    clickView('Archived');
    await screen.findByText('a put-away idea');
    listNotes.mockReturnValue(deferred<unknown[]>().promise);
    clickView('Active');

    // 'a live idea' is what the user last SAW; the newer server copy only
    // lands when its request does.
    expect(screen.getByText('a live idea')).toBeTruthy();
  });

  it('a filter change invalidates both snapshots', async () => {
    // A snapshot only speaks for the filters it was taken under. Coming back
    // to a view after narrowing must not flash rows the filter excludes.
    await mount();
    clickView('Archived');
    await screen.findByText('a put-away idea');
    clickView('Active');
    await screen.findByText('a live idea');

    fireEvent.change(screen.getByPlaceholderText(/search/i), {
      target: { value: 'nothing matches' },
    });
    await waitFor(() => expect(listNotes).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: 'nothing matches' }), expect.anything(), undefined,
    ));

    listNotes.mockReturnValue(deferred<unknown[]>().promise);
    clickView('Archived');

    expect(screen.queryByText('a put-away idea')).toBeNull();
    expect(screen.getByTestId('note-timeline-skeleton')).toBeTruthy();
  });
});
