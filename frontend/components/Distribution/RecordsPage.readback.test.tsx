/**
 * "Published" is two different claims, and the page used to make only one.
 *
 * Production, 2026-08-15. A user published an image post; it was live on the
 * platform. Their work item sat at `in_progress` with an empty description,
 * and the records row said "Published" with nothing more. The system was in
 * fact doing the right thing — `publish_readback` waits out a grace period and
 * then confirms go-live from the creator centre — but nothing anywhere said
 * so, so the correct wait was indistinguishable from a hang. The user
 * concluded the publish system was broken.
 *
 * `status: 'success'` means OUR upload finished. Whether the platform put it
 * live is `verify_state`, and these tests pin the four answers apart.
 *
 * ⚠️ The hard one is `not_live` vs `pending`. `not_live` means we looked and it
 * is not up; `pending` (and `null`, its starting point) means this round could
 * not tell us — a crashed browser container reads exactly like that. Merging
 * them renders our own outage as "the platform rejected your post". The cost is
 * wildly asymmetric, which is why `publish_readback.py`'s module docstring
 * calls it out and why there is a test here for each one separately.
 */
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

/**
 * i18next is never initialised under vitest, so its `t` hands back the default
 * string VERBATIM — `{{first}}` and all. Left alone, the timing assertions
 * below would be unprovable either way: asserting on "10 minutes" fails on
 * correct code, and asserting on the literal `{{first}}` passes even if the
 * component forgot to supply the numbers at all. That second one is the
 * dangerous shape — a test that cannot go red.
 *
 * So the stub interpolates. What it then proves is exactly the thing worth
 * proving here: the component feeds i18n the numbers it got from the backend.
 * Doing the substitution itself is i18next's job and is not under test.
 */
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string, opts?: Record<string, unknown>) =>
      Object.entries(opts ?? {}).reduce(
        (acc, [name, value]) => acc.replaceAll(`{{${name}}}`, String(value)),
        fallback ?? key,
      ),
  }),
}));

// `vi.mock` is hoisted above every top-level const, so the fixture builders it
// calls have to be hoisted with it.
const { getReadbackTiming, account, batch } = vi.hoisted(() => ({
  // Our own cadence, as the backend projects it from publish_readback's
  // constants: first look 10 min after go-live, give up after 130 min.
  getReadbackTiming: vi.fn().mockResolvedValue({
    first_check_after_seconds: 600,
    give_up_after_seconds: 7800,
  }),
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
    published_at: '2026-08-15T03:09:59Z',
    verify_state: null,
    verify_detail: null,
    ...extra,
  }),
  batch: (id: string, title: string, accounts: unknown[]) => ({
    id,
    content_type: 'images',
    title,
    description: null,
    topics: [],
    visibility: 'public',
    distribution_mode: 'broadcast',
    status: 'success',
    created_at: '2026-08-15T03:09:39Z',
    scheduled_at: null,
    schedule_state: 'none',
    accounts,
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
    // Exactly the production row: session channel, upload succeeded, nobody
    // has read it back yet, so both verify columns are NULL.
    batch('900', 'Never Checked', [account('30', 'Fresh', {})]),
    batch('901', 'Confirmed', [account('31', 'Live', { verify_state: 'verified' })]),
    batch('902', 'Refused', [account('32', 'Rejected', {
      verify_state: 'not_live',
      verify_detail: '[rejected] the platform refused this post',
    })]),
    batch('903', 'Under Review', [account('33', 'Reviewing', {
      verify_state: 'not_live',
      verify_detail: '[under_review] the platform is still reviewing this work',
    })]),
    batch('904', 'Never Answered', [account('34', 'Unknown', {
      verify_state: 'abandoned',
      verify_detail: '[verification_abandoned] gave up after 5 attempt(s); last failure: [timeout] no response',
    })]),
    batch('905', 'No Readback Here', [account('35', 'Unsupported', {
      verify_state: 'not_supported',
      verify_detail: '[not_supported] this platform has no publish read-back',
    })]),
    // An OAuth row gets its URL from the API that created it and is never read
    // back — showing it as "awaiting confirmation" would invent a wait that is
    // not happening. Mirrors the backend's `_readback_columns` scoping.
    batch('906', 'Official Channel', [account('36', 'ApiPosted', {
      channel: 'official',
      published_url: 'https://douyin/v/1',
    })]),
  ]),
  cancelPublishTask: vi.fn(),
  retryPublishTask: vi.fn().mockResolvedValue({}),
  getShareSchema: vi.fn(),
  getReadbackTiming,
  listAccounts: vi.fn().mockResolvedValue(
    ['30', '31', '32', '33', '34', '35', '36'].map((id) => ({
      id, scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: `op${id}`,
      username: `acct-${id}`, avatar_url: null, token_expires_at: null, status: 'active',
      created_at: '2026-08-15T00:00:00Z',
    })),
  ),
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../../contexts/TaskManagerContext', () => ({ useTaskManager: () => ({ tasks: [] }) }));

import RecordsPage from './RecordsPage';

/**
 * Expand one batch and return a scope limited to its account rows.
 *
 * Scoped rather than page-wide because the collapsed card header repeats the
 * single account's name ("Unsupported (Douyin)"), so a page-wide query for a
 * username matches twice and every assertion becomes ambiguous.
 */
const openBatch = async (title: string) => {
  const { container } = render(<MemoryRouter><RecordsPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(title)).toBeInTheDocument());
  fireEvent.click(screen.getByText(title));
  const sub = await waitFor(() => {
    const el = container.querySelector('.rec-sub');
    if (!el) throw new Error('account rows did not expand');
    return el as HTMLElement;
  });
  return within(sub);
};

describe('RecordsPage — what the platform says, vs what we did', () => {
  it('says it is waiting for the platform, and roughly how long', async () => {
    const row = await openBatch('Never Checked');

    expect(await row.findByText(/Awaiting platform confirmation/i)).toBeInTheDocument();
    // The numbers come from the backend's own constants (600s → 10 min,
    // 7800s → 130 min). Writing them into the frontend would be a second
    // declaration, and the next step is always telling the user the PLATFORM
    // promised them.
    //
    // `findByText`, not `getByText`: the cadence is fetched separately from the
    // records list precisely so it cannot take the page down, which also means
    // it lands a tick later than the chip.
    expect(await row.findByText(/about 10 minutes later/i)).toBeInTheDocument();
    expect(await row.findByText(/up to 130 minutes/i)).toBeInTheDocument();
  });

  it('does not claim confirmation it has not got', async () => {
    const row = await openBatch('Never Checked');
    await row.findByText(/Awaiting platform confirmation/i);

    // The upload status is still shown (there is nothing to re-send), but the
    // platform-side claim is not made.
    expect(row.queryByText(/Live on platform/i)).toBeNull();
  });

  it('says so once the platform has confirmed it', async () => {
    const row = await openBatch('Confirmed');

    expect(await row.findByText(/Live on platform/i)).toBeInTheDocument();
    expect(row.queryByText(/Awaiting platform confirmation/i)).toBeNull();
  });

  it('reports a refusal as a refusal, with the reason', async () => {
    const row = await openBatch('Refused');

    expect(await row.findByText(/Not live on platform/i)).toBeInTheDocument();
    // Typed reason → translated copy. The backend's own English is written for
    // logs and stays on the title attribute.
    expect(row.getByText(/platform refused this post/i)).toBeInTheDocument();
    expect(row.queryByText(/Could not confirm/i)).toBeNull();
  });

  it('distinguishes "still under review" from "refused"', async () => {
    const row = await openBatch('Under Review');

    expect(await row.findByText(/still reviewing this post/i)).toBeInTheDocument();
    // Both are `not_live`, but the user's move differs completely: wait vs
    // appeal-and-republish. Collapsing the reason would make the row
    // unactionable.
    expect(row.queryByText(/platform refused this post/i)).toBeNull();
  });

  it('never dresses "we could not find out" as "the platform said no"', async () => {
    // THE load-bearing one. `abandoned` means our read-back never got an
    // answer — most often our own container was down. Rendering that as a
    // rejection blames the platform for our outage and sends the user off to
    // appeal something that never happened.
    const row = await openBatch('Never Answered');

    expect(await row.findByText(/Could not confirm/i)).toBeInTheDocument();
    expect(row.queryByText(/Not live on platform/i)).toBeNull();
    expect(row.queryByText(/platform refused/i)).toBeNull();
    // And it says the post may well be live, because it may well be.
    expect(row.getByText(/may well be live/i)).toBeInTheDocument();
  });

  it('does not hold a row hostage to a read-back we never implemented', async () => {
    // A platform with no read-back is OUR coverage gap. Parking the user's row
    // in "awaiting" forever would punish them for it — same call the backend's
    // `readback_verdict` makes with `not_supported`.
    const row = await openBatch('No Readback Here');

    // The account cell renders "name · Platform" in one node, hence the regex.
    expect(await row.findByText(/Unsupported/)).toBeInTheDocument();
    expect(row.queryByText(/Awaiting platform confirmation/i)).toBeNull();
    expect(row.queryByText(/Could not confirm/i)).toBeNull();
  });

  it('invents no wait for channels that are never read back', async () => {
    const row = await openBatch('Official Channel');

    expect(await row.findByText(/ApiPosted/)).toBeInTheDocument();
    expect(row.queryByText(/Awaiting platform confirmation/i)).toBeNull();
  });
});

describe('RecordsPage — when the timing itself cannot be fetched', () => {
  it('still says what it is waiting for, just without the numbers', async () => {
    // Losing the cadence costs one clause; substituting a guessed number costs
    // the honesty of the whole sentence. So the copy drops the numbers.
    getReadbackTiming.mockResolvedValueOnce(null);
    const row = await openBatch('Never Checked');

    expect(await row.findByText(/Awaiting platform confirmation/i)).toBeInTheDocument();
    expect(row.getByText(/We check the platform afterwards/i)).toBeInTheDocument();
    expect(row.queryByText(/minutes later/i)).toBeNull();
  });
});
