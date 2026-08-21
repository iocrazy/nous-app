/**
 * Two ways this page answered a question with something that was not true.
 *
 * ── 1. A scheduled post that had not gone out yet read "Published" ──────────
 *
 * Production, reported by the user while looking at it. Three batches, two
 * published immediately and one scheduled two hours out. All three said
 * "Published", and the scheduled one added "Awaiting platform confirmation"
 * with "we check about 10 minutes from now". None of that was true of it: the
 * platform had it queued, not posted, and our read-back was not going to look
 * for another two hours plus ten minutes — `publish_readback.go_live_at`
 * measures its grace from `scheduled_at`, which it has always done correctly.
 * The backend was right; the page was reading `status: 'success'` (WE finished
 * the upload) as though it meant "it is on the platform".
 *
 * ── 2. The records page and Task Center disagreed about one batch ───────────
 *
 * Same session: the records row said "Queued" while Task Center said "Running
 * 2s" about the same publish. Not a refresh lag — nothing in the backend ever
 * writes `publish_task_accounts.status = 'publishing'` (rows go `pending` →
 * `success`/`failed` in one hop), so a batch stays "Queued" on this page for
 * the whole run, however long the upload takes. Two views, one event, two
 * answers, indefinitely.
 *
 * ⚠️ Why the fixtures below matter more than the assertions. The blind spot was
 * never the rendering — it was that no fixture anywhere in this repo carried
 * `scheduled_at` in the FUTURE with `status: 'success'`. Every read-back test
 * builds `scheduled_at: null`. The state the user hit had never been rendered
 * by a test at all, so no test could have gone red for it.
 *
 * Each pairing below therefore carries its own control (an immediate batch, an
 * elapsed schedule, a batch with no running workflow) so the assertions prove
 * a DIFFERENCE rather than a constant — the two states have to differ in
 * wording and in colour, because "the same chip with a different word" is how
 * a test passes on the broken version too.
 */
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

// Interpolating stub, same reasoning as RecordsPage.readback.test.tsx: without
// it, an assertion on "10 minutes after that" fails on correct code while an
// assertion on the literal `{{first}}` passes on code that never supplied the
// numbers. The second shape is a test that cannot go red.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string, opts?: Record<string, unknown>) =>
      Object.entries(opts ?? {}).reduce(
        (acc, [name, value]) => acc.replaceAll(`{{${name}}}`, String(value)),
        fallback ?? key,
      ),
  }),
}));

const {
  getReadbackTiming, trackedTasks, account, batch, FUTURE_GO_LIVE, ELAPSED_GO_LIVE,
} = vi.hoisted(() => ({
  /** A go-live far enough out that it is still ahead of the clock in any CI
   *  timezone, and on a date that shares no digits with the upload date below
   *  — so "the sentence quotes the go-live time" is provable, not assumed.
   *  Noon UTC keeps the calendar day the same either side of the date line. */
  FUTURE_GO_LIVE: '2099-12-25T12:00:00Z',
  /** Same shape, already past. The override has to key on the CLOCK, not on
   *  the mere presence of a scheduled_at. */
  ELAPSED_GO_LIVE: '2026-08-14T12:00:00Z',
  // The backend's own constants, as `/capabilities` projects them: first look
  // 10 min after GO-LIVE, give up 130 min after that.
  getReadbackTiming: vi.fn().mockResolvedValue({
    first_check_after_seconds: 600,
    give_up_after_seconds: 7800,
  }),
  trackedTasks: [] as unknown[],
  account: (id: string, username: string, extra: Record<string, unknown>) => ({
    id,
    account_id: id,
    username,
    avatar_url: null,
    channel: 'session',
    status: 'success',
    error_message: null,
    published_url: null,
    platform_item_id: null,
    // When WE finished the upload — deliberately not the go-live time, which
    // is the whole distinction under test.
    published_at: '2026-08-18T00:59:18Z',
    verify_state: null,
    verify_detail: null,
    ...extra,
  }),
  batch: (
    id: string,
    title: string,
    accounts: unknown[],
    extra: Record<string, unknown> = {},
  ) => ({
    id,
    content_type: 'video',
    title,
    description: null,
    topics: [],
    visibility: 'public',
    distribution_mode: 'broadcast',
    status: 'success',
    created_at: '2026-08-18T00:58:48Z',
    scheduled_at: null,
    schedule_state: 'none',
    self_declaration: null,
    collection_name: null,
    music_name: null,
    music_ref: null,
    accounts,
    ...extra,
  }),
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
    // ── the production row ──
    batch('700', 'Scheduled Ahead', [account('40', 'Waiting', {})], {
      scheduled_at: FUTURE_GO_LIVE,
      schedule_state: 'pending',
    }),
    // Control: published immediately. Read-back genuinely starts ~10 min from
    // the upload, so the OLD copy is the right copy here and must survive.
    batch('701', 'Immediate Post', [account('41', 'Sent', {})]),
    // Control: it was scheduled, and that time has come and gone. Identical
    // row shape to 'Scheduled Ahead' apart from the clock.
    batch('702', 'Schedule Elapsed', [account('42', 'Elapsed', {})], {
      scheduled_at: ELAPSED_GO_LIVE,
      schedule_state: 'unreachable',
    }),
    // ── the two-views disagreement ──
    batch('703', 'Running Now', [account('43', 'Uploading', { status: 'pending', published_at: null })], {
      status: 'pending',
    }),
    // Control: same business rows, no workflow executing.
    batch('704', 'Waiting Turn', [account('44', 'NotStarted', { status: 'pending', published_at: null })], {
      status: 'pending',
    }),
    // The engine stamped this batch's id as a JSON number rather than the
    // string the live rows carry. Same batch, same fact; a join that compares
    // raw values silently decides nothing is running.
    batch('705', 'Numeric Metadata', [account('45', 'AlsoUploading', { status: 'pending', published_at: null })], {
      status: 'pending',
    }),
    // A scheduled batch the platform HAS since confirmed. A terminal verdict
    // outranks our clock — if the platform says it is up, it is up.
    batch('706', 'Confirmed Early', [account('46', 'LiveAnyway', { verify_state: 'verified' })], {
      scheduled_at: FUTURE_GO_LIVE,
      schedule_state: 'pending',
    }),
  ]),
  cancelPublishTask: vi.fn(),
  retryPublishTask: vi.fn().mockResolvedValue({}),
  getShareSchema: vi.fn(),
  getReadbackTiming,
  isScheduleUnreachable: vi.fn().mockReturnValue(false),
  listAccounts: vi.fn().mockResolvedValue(
    ['40', '41', '42', '43', '44', '45', '46'].map((id) => ({
      id, scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: `op${id}`,
      username: `acct-${id}`, avatar_url: null, token_expires_at: null, status: 'active',
      created_at: '2026-08-18T00:00:00Z',
    })),
  ),
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: trackedTasks }),
}));

import RecordsPage, { accountChipFor, pendingGoLiveAt, taskChipFor } from './RecordsPage';

const renderPage = async () => {
  const { container } = render(<MemoryRouter><RecordsPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText('Scheduled Ahead')).toBeInTheDocument());
  return container;
};

/** The whole card for one batch — the collapsed view a user sees first, which
 *  is where the "Published" claim was made. */
const cardFor = (container: HTMLElement, title: string): HTMLElement => {
  const card = Array.from(container.querySelectorAll('.rec')).find(
    (c) => c.querySelector('.info b')?.textContent === title,
  );
  if (!card) throw new Error(`no card titled ${title}`);
  return card as HTMLElement;
};

/** The status chip on the card header (not the per-account ones inside). */
const cardChip = (container: HTMLElement, title: string): HTMLElement => {
  const chip = cardFor(container, title).querySelector('.rec-head > .chip');
  if (!chip) throw new Error(`card ${title} has no status chip`);
  return chip as HTMLElement;
};

/** The chips on one batch's single account row. The account name and the
 *  platform share a node ("Waiting · Douyin"), so this reaches for the row
 *  itself rather than trying to match half of that text. */
const rowChips = (container: HTMLElement, title: string): HTMLElement[] => {
  const row = cardFor(container, title).querySelector('.sub-row');
  if (!row) throw new Error(`batch ${title} has no expanded account row`);
  return Array.from(row.querySelectorAll('.chip')) as HTMLElement[];
};

/** Expand a batch and scope queries to its account rows. */
const openBatch = async (container: HTMLElement, title: string) => {
  fireEvent.click(screen.getByText(title));
  const sub = await waitFor(() => {
    const el = cardFor(container, title).querySelector('.rec-sub');
    if (!el) throw new Error('account rows did not expand');
    return el as HTMLElement;
  });
  return within(sub);
};

describe('RecordsPage — a scheduled post is not a published post', () => {
  it('does not call a batch the platform has not posted yet "Published"', async () => {
    const container = await renderPage();
    const chip = cardChip(container, 'Scheduled Ahead');

    // Both axes, because a chip that changed only its word would still be a
    // green "done" badge sitting next to a post that is not out.
    expect(chip.textContent).toContain('Scheduled');
    expect(chip.textContent).not.toContain('Published');
    expect(chip.className).toContain('chip-violet');
    expect(chip.className).not.toContain('chip-green');
  });

  it('still calls an immediate publish "Published"', async () => {
    // The control that keeps the fix from being "never say Published".
    const container = await renderPage();
    const chip = cardChip(container, 'Immediate Post');

    expect(chip.textContent).toContain('Published');
    expect(chip.className).toContain('chip-green');
  });

  it('goes back to "Published" once the scheduled time has passed', async () => {
    // Same row shape as 'Scheduled Ahead'; only the clock differs. If this one
    // also read "Scheduled", the override would be keying on the presence of a
    // scheduled_at and would park every scheduled batch there forever.
    const container = await renderPage();
    const chip = cardChip(container, 'Schedule Elapsed');

    expect(chip.textContent).toContain('Published');
    expect(chip.className).toContain('chip-green');
  });

  it('names the go-live time instead of claiming a confirmation is pending', async () => {
    const container = await renderPage();
    const row = await openBatch(container, 'Scheduled Ahead');

    // "Awaiting platform confirmation" says the platform has it and we are
    // waiting on a verdict. Before go-live there is no verdict coming.
    expect(row.queryByText(/Awaiting platform confirmation/i)).toBeNull();
    // Dec 25 is the GO-LIVE date; the upload happened in August. Asserting on
    // the date proves the sentence quotes the right clock, not merely that
    // some time was interpolated.
    expect(await row.findByText(/Goes live .*Dec/i)).toBeInTheDocument();
  });

  it('says outright that it is not public yet, and when we will check', async () => {
    const container = await renderPage();
    const row = await openBatch(container, 'Scheduled Ahead');

    // The user's actual question was "is it up?". This is the answer.
    expect(await row.findByText(/not public yet/i)).toBeInTheDocument();
    // Anchored to go-live: 10 minutes AFTER that, not 10 minutes from now.
    // The old sentence ("about 10 minutes later", counted from the upload)
    // was wrong by the whole length of the schedule.
    expect(row.getByText(/does not publish it until .*Dec/i)).toBeInTheDocument();
    expect(row.getByText(/about 10 minutes after that/i)).toBeInTheDocument();
    expect(row.getByText(/up to 130 minutes/i)).toBeInTheDocument();
    expect(row.queryByText(/about 10 minutes later/i)).toBeNull();
  });

  it('keeps the immediate-publish sentence counting from the upload', async () => {
    const container = await renderPage();
    const row = await openBatch(container, 'Immediate Post');

    expect(await row.findByText(/Awaiting platform confirmation/i)).toBeInTheDocument();
    expect(row.getByText(/about 10 minutes later/i)).toBeInTheDocument();
    expect(row.queryByText(/not public yet/i)).toBeNull();
  });

  it('does not repeat "Published" on the account row either', async () => {
    const container = await renderPage();
    await openBatch(container, 'Scheduled Ahead');

    const chips = rowChips(container, 'Scheduled Ahead');
    expect(chips.map((c) => c.textContent).join(' ')).not.toContain('Published');
    expect(chips.some((c) => c.className.includes('chip-green'))).toBe(false);
  });

  it('lets a platform verdict outrank our clock', async () => {
    // If the platform says it is live, it is live — whatever our copy of the
    // schedule says. The clock only decides what to render in the ABSENCE of
    // an answer, which is the same precedence `verifyDisplayFor` gives every
    // other terminal verdict.
    const container = await renderPage();
    const row = await openBatch(container, 'Confirmed Early');

    expect(await row.findByText(/Live on platform/i)).toBeInTheDocument();
    expect(row.queryByText(/Goes live/i)).toBeNull();
  });
});

describe('RecordsPage — one running publish, one answer', () => {
  const running = (publishTaskId: string | number) => ({
    id: `wf-${publishTaskId}`,
    dbos_workflow_id: `wf-${publishTaskId}`,
    user_id: 'u1',
    task_type: 'publish',
    status: 'processing',
    phase: 'processing',
    title: 'Publish: x',
    progress: 0,
    metadata: { publish_task_id: publishTaskId, channel: 'session' },
    created_at: '2026-08-18T00:58:48Z',
  });

  it('says "Publishing" while the engine says the workflow is running', async () => {
    // `publish_task_accounts` never leaves 'pending' during a run, so without
    // consulting task_tracking this card reads "Queued" for the entire upload
    // — which is exactly what Task Center was contradicting.
    trackedTasks.length = 0;
    trackedTasks.push(running('703'));
    const container = await renderPage();
    const chip = cardChip(container, 'Running Now');

    expect(chip.textContent).toContain('Publishing');
    expect(chip.textContent).not.toContain('Queued');
    // Second axis: the in-flight chip is the pulsing indigo one, not the muted
    // idle one. A word swap alone would leave the card looking idle.
    expect(chip.className).toContain('chip-indigo');
    expect(chip.className).toContain('pulse');
    expect(chip.className).not.toContain('chip-mute');
  });

  it('still says "Queued" for a batch with no workflow running', async () => {
    trackedTasks.length = 0;
    trackedTasks.push(running('703'));
    const container = await renderPage();
    const chip = cardChip(container, 'Waiting Turn');

    expect(chip.textContent).toContain('Queued');
    expect(chip.className).toContain('chip-mute');
  });

  it('matches the batch whatever JSON type the id arrived as', async () => {
    // The live `task_tracking` rows stamp `publish_task_id` as a string, and
    // `PublishTask.id` is a string — but they are written by two different
    // code paths. A raw `===` degrades to "nothing is ever running", the
    // silent-no-op shape of the string-vs-number id bug this repo has already
    // paid for once.
    trackedTasks.length = 0;
    trackedTasks.push(running(705));
    const container = await renderPage();
    const chip = cardChip(container, 'Numeric Metadata');

    expect(chip.textContent).toContain('Publishing');
    expect(chip.className).toContain('chip-indigo');
  });

  it('does not claim the account row itself is uploading', async () => {
    // The workflow walks its accounts one after another and reports nothing
    // per row, so "this batch is running" does not establish that THIS account
    // is the one being uploaded. Painting every row "Publishing" off a
    // batch-level signal would invent a precision we do not have — the same
    // move as rendering `abandoned` (we never saw it) as `not_live` (we looked
    // and it is gone).
    trackedTasks.length = 0;
    trackedTasks.push(running('703'));
    const container = await renderPage();
    await openBatch(container, 'Running Now');

    const chips = rowChips(container, 'Running Now');
    expect(chips.map((c) => c.textContent).join(' ')).toContain('Queued');
  });

  it('ignores a workflow that is not a publish, and one that has finished', async () => {
    trackedTasks.length = 0;
    trackedTasks.push(
      { ...running('703'), task_type: 'download' },
      { ...running('704'), status: 'completed', phase: 'completed' },
    );
    const container = await renderPage();

    expect(cardChip(container, 'Running Now').textContent).toContain('Queued');
    expect(cardChip(container, 'Waiting Turn').textContent).toContain('Queued');
  });
});

describe('pendingGoLiveAt — the edges the rendering tests cannot reach', () => {
  const NOW = Date.parse('2026-08-18T01:02:00Z');

  it('reports the moment while it is still ahead', () => {
    expect(pendingGoLiveAt('2026-08-18T03:10:00Z', NOW)).toBe(Date.parse('2026-08-18T03:10:00Z'));
  });

  it('says nothing for an immediate publish', () => {
    expect(pendingGoLiveAt(null, NOW)).toBeNull();
    expect(pendingGoLiveAt(undefined, NOW)).toBeNull();
  });

  it('says nothing once the moment has arrived', () => {
    // Exactly on the boundary counts as arrived: at `scheduled_at` the
    // platform is publishing, so there is no longer a future to announce.
    expect(pendingGoLiveAt('2026-08-18T01:02:00Z', NOW)).toBeNull();
    expect(pendingGoLiveAt('2026-08-18T01:01:59Z', NOW)).toBeNull();
  });

  it('says nothing rather than NaN for a timestamp it cannot read', () => {
    // The copy then omits the clause. Rendering "Goes live NaN" or, worse,
    // inventing a wait off an unreadable value would both be claims we have no
    // basis for — same call `describeSessionFreshness` makes for `unknown`.
    expect(pendingGoLiveAt('not a date', NOW)).toBeNull();
    expect(pendingGoLiveAt('', NOW)).toBeNull();
  });
});

describe('the chip functions keep the two overrides apart', () => {
  it('does not let a running workflow relabel a scheduled batch', () => {
    // Both signals at once is a real combination: a scheduled batch whose
    // workflow is re-dispatched (a retry). It is scheduled AND running, and
    // the word the user needs is the one about whether the post is public.
    const chip = taskChipFor({ status: 'success', goLivePending: true, workflowRunning: true });
    expect(chip.fallback).toBe('Scheduled');
  });

  it('does not let a schedule relabel a batch that is still uploading', () => {
    // The upload has not finished, so "Scheduled" would claim the platform has
    // it. It does not yet.
    const chip = taskChipFor({ status: 'pending', goLivePending: true, workflowRunning: true });
    expect(chip.fallback).toBe('Publishing');
  });

  it('leaves failures and hand-offs alone', () => {
    for (const status of ['failed', 'partial', 'pending_share'] as const) {
      expect(
        taskChipFor({ status, goLivePending: true, workflowRunning: true }).fallback,
      ).toBe(
        taskChipFor({ status, goLivePending: false, workflowRunning: false }).fallback,
      );
    }
  });

  it('only overrides a per-account row that actually succeeded', () => {
    expect(accountChipFor({ status: 'success', goLivePending: true }).fallback).toBe('Scheduled');
    expect(accountChipFor({ status: 'failed', goLivePending: true }).fallback).toBe('Failed');
    expect(accountChipFor({ status: 'cancelled', goLivePending: true }).fallback).toBe('Cancelled');
  });
});
