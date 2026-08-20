/**
 * A batch whose track would not select must not be answered with advice that
 * cannot work, nor with a button that repeats the same attempt under a name
 * that hides it.
 *
 * Production, 2026-08-16. A failed images post read:
 *
 *   "The platform's music search found nothing for that name — nothing was
 *    published. Try another spelling."   [Retry]
 *
 * Both halves were wrong for that row. The track had been picked out of the
 * platform's own catalogue panel, and a read-only probe of the catalogue API
 * returns it for the typed form, the de-punctuated form and the author's name
 * alike — so spelling is measurably not the lever. And "Retry" re-sends the
 * stored track, which this page cannot edit, so it runs the byte-identical
 * search that just came back empty.
 *
 * What these tests pin down:
 *
 *   1. the dead advice is gone,
 *   2. the row offers the route that is known to work (publish with no track),
 *   3. the option that repeats the attempt is still there but says so,
 *   4. the strong claim ("the name is not the problem") appears ONLY where it
 *      is established — a track picked from the catalogue, not one typed from
 *      memory, and
 *   5. a batch that hit BOTH walls (expired schedule + unselectable track) is
 *      offered the one combination that can succeed.
 *
 * ⚠️ Every fixture below carries `music_name`/`music_ref` on the BATCH and the
 * `[music_not_found]` reason on the ACCOUNT row, which is where the real wire
 * puts them. No test in this suite carried that shape before, which is exactly
 * why the branch could ship broken.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Hoisted with the spies because `vi.mock`'s factory runs before any top-level
// const in this file exists.
const { retryPublishTask, isScheduleUnreachable, TRACK, MUSIC_FAILURE } = vi.hoisted(() => ({
  retryPublishTask: vi.fn().mockResolvedValue({}),
  isScheduleUnreachable: vi.fn().mockReturnValue(false),
  /** The catalogue card shape the picker stores (mig 429). `music_id` is the
   *  platform's `id_str` — a STRING, because the sibling numeric id is past
   *  2^53 and is already a different number by the time it reaches a client. */
  TRACK: {
    music_id: '7013259661235619592',
    music_name: 'Rooftop Rain',
    music_author: 'Night Signal',
    duration: 178,
    user_count: 41207,
    cover_url: 'https://p3.example.invalid/cover.jpeg',
  },
  /** `[reason] message` is what `publish_distribution._finish_account` writes;
   *  the trailing bracket is the row probe added alongside the reason. */
  MUSIC_FAILURE:
    "[music_not_found] no music named 'Rooftop Rain' came back from the platform's "
    + 'search (no row matched the picked track) [rows=0]',
}));

vi.mock('../../services/distributionService', () => ({
  // 榜单缓存：面板打开时读。默认给"从没采过"——那是一个真实且常见的状态
  // （新账号、刚上线），而且它必须与"读失败"分得开，所以这里不是空数组。
  fetchMusicCharts: vi.fn().mockResolvedValue({
    charts: [], last_success_at: null, stale: true, never_harvested: true, ttl_hours: 24,
  }),
  refreshMusicCharts: vi.fn().mockResolvedValue({ success: true }),
  // 真实实现，不是 stub：页面拿它的返回值当 React key，而「推荐」和「收藏」
  // 在真实面板上共用 category_id='1'——给个只返回 id 的 stub 会让两个 tab 撞 key。
  musicChartKey: (c: { category_kind: string; category_id: string }) =>
    `${c.category_kind}:${c.category_id}`,
  listPublishTasks: vi.fn().mockResolvedValue([
    {
      // Picked from the catalogue panel → the name is not in question.
      id: '901',
      content_type: 'images',
      title: 'Catalogue Pick',
      description: null,
      topics: [],
      visibility: 'public',
      distribution_mode: 'broadcast',
      status: 'failed',
      created_at: '2026-08-16T05:06:59Z',
      scheduled_at: null,
      schedule_state: 'none',
      self_declaration: null,
      collection_name: null,
      music_name: TRACK.music_name,
      music_ref: TRACK,
      accounts: [{
        id: '31', account_id: '40', username: 'Catalogue One', avatar_url: null,
        channel: 'session', status: 'failed', error_message: MUSIC_FAILURE,
        published_url: null, platform_item_id: null, published_at: null,
        verify_state: null, verify_detail: null,
      }],
    },
    {
      // Typed from memory: same wall, but a wrong name IS possible here, so the
      // catalogue sentence must not appear. Without this row a change that
      // printed the strong claim unconditionally would pass.
      id: '902',
      content_type: 'video',
      title: 'Typed Name',
      description: null,
      topics: [],
      visibility: 'public',
      distribution_mode: 'broadcast',
      status: 'failed',
      created_at: '2026-08-16T05:06:59Z',
      scheduled_at: null,
      schedule_state: 'none',
      self_declaration: null,
      collection_name: null,
      music_name: 'Rooftop Rain',
      music_ref: null,
      accounts: [{
        id: '32', account_id: '41', username: 'Typed One', avatar_url: null,
        channel: 'session', status: 'failed', error_message: MUSIC_FAILURE,
        published_url: null, platform_item_id: null, published_at: null,
        verify_state: null, verify_detail: null,
      }],
    },
    {
      // Control. A failure with nothing to do with music — plain Retry is
      // exactly right and must survive. Without this, a change that simply
      // replaced Retry everywhere would pass.
      id: '903',
      content_type: 'video',
      title: 'Ordinary Failure',
      description: null,
      topics: [],
      visibility: 'public',
      distribution_mode: 'broadcast',
      status: 'failed',
      created_at: '2026-08-16T05:06:59Z',
      scheduled_at: null,
      schedule_state: 'none',
      self_declaration: null,
      collection_name: null,
      music_name: null,
      music_ref: null,
      accounts: [{
        id: '33', account_id: '42', username: 'Ordinary One', avatar_url: null,
        channel: 'session', status: 'failed', error_message: 'upload rejected',
        published_url: null, platform_item_id: null, published_at: null,
        verify_state: null, verify_detail: null,
      }],
    },
    {
      // Both walls at once. Neither "Publish now" nor "Publish without music"
      // can succeed on its own here.
      id: '904',
      content_type: 'images',
      title: 'Both Walls',
      description: null,
      topics: [],
      visibility: 'public',
      distribution_mode: 'broadcast',
      status: 'failed',
      created_at: '2026-08-16T05:06:59Z',
      scheduled_at: '2026-08-16T07:20:00Z',
      schedule_state: 'unreachable',
      self_declaration: null,
      collection_name: null,
      music_name: TRACK.music_name,
      music_ref: TRACK,
      accounts: [{
        id: '34', account_id: '43', username: 'Both One', avatar_url: null,
        channel: 'session', status: 'failed', error_message: MUSIC_FAILURE,
        published_url: null, platform_item_id: null, published_at: null,
        verify_state: null, verify_detail: null,
      }],
    },
  ]),
  cancelPublishTask: vi.fn(),
  retryPublishTask,
  isScheduleUnreachable,
  getShareSchema: vi.fn(),
  getReadbackTiming: vi.fn().mockResolvedValue(null),
  listAccounts: vi.fn().mockResolvedValue(
    [['40', 'Catalogue One'], ['41', 'Typed One'], ['42', 'Ordinary One'], ['43', 'Both One']]
      .map(([id, username]) => ({
        id, scope_type: 'user', scope_id: 'u1', platform: 'douyin',
        platform_user_id: `op${id}`, username, avatar_url: null,
        token_expires_at: null, status: 'active', created_at: '2026-08-16T00:00:00Z',
      })),
  ),
}));

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));
vi.mock('../../contexts/TaskManagerContext', () => ({ useTaskManager: () => ({ tasks: [] }) }));

import RecordsPage, { planFailedRow, isMusicNotFound } from './RecordsPage';
import en from '../../public/locales/en.json';
import zh from '../../public/locales/zh.json';

/** Expand one batch card and wait for its account rows to arrive. */
const openBatch = async (title: string) => {
  render(<MemoryRouter><RecordsPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(title)).toBeInTheDocument());
  fireEvent.click(screen.getByText(title));
};

describe('RecordsPage — a track the platform would not select', () => {
  beforeEach(() => {
    retryPublishTask.mockClear();
    addToast.mockClear();
    isScheduleUnreachable.mockReturnValue(false);
  });

  it('stops telling the user to re-spell a track it took from the catalogue', async () => {
    await openBatch('Catalogue Pick');
    await screen.findByText(/returned nothing for this track/i);

    // The sentence the user actually read. It survived because nothing in the
    // suite ever rendered a music failure.
    expect(screen.queryByText(/another spelling/i)).toBeNull();
  });

  it('says what IS established, and stops there', async () => {
    await openBatch('Catalogue Pick');

    // Established: the track came out of the platform's own catalogue.
    expect(
      await screen.findByText(/picked this track from the platform's own catalogue/i),
    ).toBeInTheDocument();
    // NOT established: which side of the dialog lost it. The copy has to say
    // so out loud — writing an open question up as a diagnosis is what put the
    // dead advice on this row in the first place.
    expect(screen.getByText(/still being worked out/i)).toBeInTheDocument();
  });

  it('offers the route that is known to work', async () => {
    await openBatch('Catalogue Pick');
    fireEvent.click(await screen.findByRole('button', { name: /^Publish without music$/i }));

    // `dropMusic` is what clears the track server-side. Sending the retry
    // without it reproduces the original failure exactly.
    await waitFor(() => expect(retryPublishTask)
      .toHaveBeenCalledWith('901', 'as_scheduled', { dropMusic: true }));
  });

  it('keeps the same-track attempt available, under a label that admits what it is', async () => {
    await openBatch('Catalogue Pick');

    // A bare "Retry" here would hide the one thing we do know: nothing about
    // the attempt changes.
    expect(screen.queryAllByRole('button', { name: /^Retry$/i })).toHaveLength(0);
    expect(await screen.findByText(/repeats the same search/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Try the same track again$/i }));
    await waitFor(() => expect(retryPublishTask)
      .toHaveBeenCalledWith('901', 'as_scheduled', { dropMusic: false }));
  });

  it('does not claim the name is fine when the user typed it from memory', async () => {
    await openBatch('Typed Name');
    await screen.findByText(/returned nothing for this track/i);

    // No catalogue card was involved, so we have no basis for ruling the name
    // out — and saying so anyway would send someone hunting a bug that isn't.
    expect(screen.queryByText(/picked this track from the platform's own catalogue/i)).toBeNull();
    // The working route is still offered: it does not depend on knowing why.
    expect(screen.getByRole('button', { name: /^Publish without music$/i })).toBeInTheDocument();
  });

  it('leaves an ordinary failure with an ordinary Retry', async () => {
    await openBatch('Ordinary Failure');
    fireEvent.click(await screen.findByRole('button', { name: /^Retry$/i }));

    await waitFor(() => expect(retryPublishTask)
      .toHaveBeenCalledWith('903', 'as_scheduled', { dropMusic: false }));
    expect(screen.queryByRole('button', { name: /without music/i })).toBeNull();
    expect(screen.queryByText(/repeats the same search/i)).toBeNull();
  });

  it('offers the one combination that can succeed when both walls are up', async () => {
    await openBatch('Both Walls');

    // Each of these on its own walks into the other wall.
    expect(screen.queryAllByRole('button', { name: /^Publish now$/i })).toHaveLength(0);
    expect(screen.queryAllByRole('button', { name: /^Publish without music$/i })).toHaveLength(0);
    // Nor a third attempt at the same track: with the schedule gone that one
    // is provably rejected, unlike the music path.
    expect(screen.queryAllByRole('button', { name: /^Try the same track again$/i })).toHaveLength(0);

    fireEvent.click(await screen.findByRole('button', { name: /^Publish now without music$/i }));
    await waitFor(() => expect(retryPublishTask)
      .toHaveBeenCalledWith('904', 'now', { dropMusic: true }));
  });
});

describe('planFailedRow', () => {
  const ALL = [false, true].flatMap((scheduleUnreachable) =>
    [false, true].flatMap((musicNotFound) =>
      [false, true].map((musicFromCatalogue) => ({
        scheduleUnreachable, musicNotFound, musicFromCatalogue,
      }))));

  it('always leaves the user something to press', () => {
    // A row with no action is the silent dead end this whole change is about.
    for (const input of ALL) {
      expect(planFailedRow(input).actions.length, JSON.stringify(input)).toBeGreaterThan(0);
    }
  });

  it('only ever drops the music when the music is what failed', () => {
    // Throwing away a track the user chose cannot be a side effect of "try
    // again" — a post's music cannot be changed once it is up.
    for (const input of ALL.filter((i) => !i.musicNotFound)) {
      expect(planFailedRow(input).actions.some((a) => a.dropMusic), JSON.stringify(input))
        .toBe(false);
    }
  });

  it('only ever drops the schedule when the schedule is unreachable', () => {
    for (const input of ALL.filter((i) => !i.scheduleUnreachable)) {
      expect(planFailedRow(input).actions.some((a) => a.mode === 'now'), JSON.stringify(input))
        .toBe(false);
    }
  });

  it('never keeps an option that is known to be turned away', () => {
    // Expired schedule = provably rejected on re-dispatch, so nothing may go
    // out `as_scheduled`. (The music wall is NOT in this category: we have not
    // established that it always loses, so that attempt is kept and labelled.)
    for (const input of ALL.filter((i) => i.scheduleUnreachable)) {
      expect(planFailedRow(input).actions.every((a) => a.mode === 'now'), JSON.stringify(input))
        .toBe(true);
    }
  });

  it('has copy in both languages for every sentence and button it can produce', () => {
    // A key with no translation renders the English fallback inside a Chinese
    // UI — silently, because i18next never complains about a missing key.
    const lookup = (bundle: unknown, key: string): unknown =>
      key.split('.').reduce<unknown>(
        (node, part) => (node && typeof node === 'object'
          ? (node as Record<string, unknown>)[part]
          : undefined),
        bundle,
      );
    for (const input of ALL) {
      const plan = planFailedRow(input);
      for (const row of [...plan.notes, ...plan.actions]) {
        expect(typeof lookup(en, row.key), `${row.key} missing in en.json`).toBe('string');
        expect(typeof lookup(zh, row.key), `${row.key} missing in zh.json`).toBe('string');
      }
    }
  });
});

describe('isMusicNotFound', () => {
  it('keys on the bracketed reason, not the prose after it', () => {
    expect(isMusicNotFound(MUSIC_FAILURE)).toBe(true);
    // The prose is written for logs and is free to change; the reason is the
    // contract. A row that merely mentions music is not this failure.
    expect(isMusicNotFound('[music_ambiguous] 5 results are indistinguishable')).toBe(false);
    expect(isMusicNotFound('the music search found nothing')).toBe(false);
    expect(isMusicNotFound(null)).toBe(false);
    expect(isMusicNotFound(undefined)).toBe(false);
  });
});
