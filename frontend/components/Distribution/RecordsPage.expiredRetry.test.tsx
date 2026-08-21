/**
 * A failed batch whose scheduled time has passed must NOT offer Retry.
 *
 * Production, 2026-08-15. A scheduled batch failed; the user pressed Retry
 * three times. All three calls returned 200 and really did dispatch a
 * workflow — and all three were rejected at the same rule, because a retry
 * reuses the `scheduled_at` stored on the batch and that moment was long gone.
 * From the page it looked like the button did nothing.
 *
 * The button was not broken. It was structurally incapable of succeeding, and
 * the page had no way of knowing that — which is what these tests pin down:
 *
 *   1. the impossible button is not rendered at all,
 *   2. what replaces it says plainly what it will do, and
 *   3. it sends the one mode the backend will accept for that batch.
 *
 * ⚠️ The fixture below is the blind spot the old suite had: no test anywhere
 * carried a failed account under an expired scheduled batch, so no test ever
 * exercised this branch.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const { retryPublishTask, isScheduleUnreachable } = vi.hoisted(() => ({
  retryPublishTask: vi.fn().mockResolvedValue({}),
  // The real classifier is unit-tested in services/distributionService.test.ts;
  // here it is a seam so a test can put the page in the "list went stale"
  // situation without constructing a DistributionApiError.
  isScheduleUnreachable: vi.fn().mockReturnValue(false),
}));

// Real wire shape: `schedule_state` is computed server-side (the threshold is
// SCHEDULE_MIN_LEAD, a backend constant), `scheduled_at` is echoed alongside
// it, and the verify_* columns exist even when NULL.
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
      id: '801',
      content_type: 'images',
      title: 'Expired Schedule',
      description: null,
      topics: [],
      visibility: 'public',
      distribution_mode: 'broadcast',
      status: 'failed',
      created_at: '2026-08-14T05:06:59Z',
      scheduled_at: '2026-08-14T07:20:00Z',
      schedule_state: 'unreachable',
      accounts: [{
        id: '11', account_id: '20', username: 'Scheduled One', avatar_url: null,
        channel: 'session', status: 'failed',
        error_message: 'publish intent rejected: scheduled_at must be at least 2 hours 10 minutes from now',
        published_url: null, platform_item_id: null, published_at: null,
        verify_state: null, verify_detail: null,
      }],
    },
    {
      // Control row. Same failure surface, schedule still inside the window —
      // Retry is exactly right here and must survive the fix. Without this a
      // change that simply deleted the Retry button would pass.
      id: '802',
      content_type: 'video',
      title: 'Still Schedulable',
      description: null,
      topics: [],
      visibility: 'public',
      distribution_mode: 'broadcast',
      status: 'failed',
      created_at: '2026-08-14T05:06:59Z',
      scheduled_at: '2026-08-20T07:20:00Z',
      schedule_state: 'pending',
      accounts: [{
        id: '12', account_id: '21', username: 'Future One', avatar_url: null,
        channel: 'session', status: 'failed', error_message: 'upload rejected',
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
  listAccounts: vi.fn().mockResolvedValue([
    { id: '20', scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: 'op1',
      username: 'Scheduled One', avatar_url: null, token_expires_at: null, status: 'active',
      created_at: '2026-08-14T00:00:00Z' },
    { id: '21', scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: 'op2',
      username: 'Future One', avatar_url: null, token_expires_at: null, status: 'active',
      created_at: '2026-08-14T00:00:00Z' },
  ]),
}));

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));
vi.mock('../../contexts/TaskManagerContext', () => ({ useTaskManager: () => ({ tasks: [] }) }));

import RecordsPage from './RecordsPage';

/** Expand one batch card and wait for its account rows to arrive. */
const openBatch = async (title: string) => {
  render(<MemoryRouter><RecordsPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(title)).toBeInTheDocument());
  fireEvent.click(screen.getByText(title));
};

describe('RecordsPage — a schedule that can no longer be honoured', () => {
  beforeEach(() => {
    retryPublishTask.mockClear();
    addToast.mockClear();
    isScheduleUnreachable.mockReturnValue(false);
  });

  it('offers no Retry button on an expired scheduled batch', async () => {
    await openBatch('Expired Schedule');
    await screen.findByText(/scheduled time for this batch has passed/i);

    // The whole point: the button that cannot work is GONE, not disabled-and-
    // hopeful, not relabelled. `queryAllByRole` over the whole page so a stray
    // Retry anywhere in this card would still be caught.
    expect(screen.queryAllByRole('button', { name: /^Retry$/i })).toHaveLength(0);
  });

  it('explains why, instead of leaving a dead end', async () => {
    await openBatch('Expired Schedule');

    // The reason is on screen in the user's language — not only in a title
    // attribute, and not the backend's log prose.
    expect(
      await screen.findByText(/scheduled time for this batch has passed/i),
    ).toBeInTheDocument();
    // A next step exists and is reachable in one click.
    expect(screen.getByRole('button', { name: /Publish now/i })).toBeInTheDocument();
  });

  it("the replacement button says what it does, and does what it says", async () => {
    await openBatch('Expired Schedule');
    fireEvent.click(await screen.findByRole('button', { name: /Publish now/i }));

    // 'now' is what drops the schedule server-side. Sending 'as_scheduled'
    // here would reproduce the original bug exactly — a 409 this time, but
    // still a button that cannot succeed. `dropMusic: false` because this
    // batch's music was never the problem: the second escape hatch must not
    // ride along with the first.
    await waitFor(() => expect(retryPublishTask)
      .toHaveBeenCalledWith('801', 'now', { dropMusic: false }));
  });

  it('leaves Retry alone while the schedule is still reachable', async () => {
    await openBatch('Still Schedulable');
    fireEvent.click(await screen.findByRole('button', { name: /^Retry$/i }));

    // Never 'now': re-timing a post the user deliberately scheduled is
    // irreversible once it is live, so it can only ever happen because they
    // pressed a button that says so.
    await waitFor(() => expect(retryPublishTask)
      .toHaveBeenCalledWith('802', 'as_scheduled', { dropMusic: false }));
    expect(screen.queryByRole('button', { name: /Publish now/i })).toBeNull();
    expect(screen.queryByText(/scheduled time for this batch has passed/i)).toBeNull();
  });

  it('says why when the deadline passed while the page sat open', async () => {
    // `schedule_state` is a snapshot from fetch time, so a page left open
    // across the deadline still shows Retry and the click lands on the
    // backend's typed 409. Answering that with the generic "Retry failed"
    // would discard a reason the backend deliberately named — the same
    // silence this whole change is about.
    retryPublishTask.mockRejectedValueOnce(new Error('409'));
    isScheduleUnreachable.mockReturnValue(true);

    await openBatch('Still Schedulable');
    fireEvent.click(await screen.findByRole('button', { name: /^Retry$/i }));

    await waitFor(() => expect(addToast).toHaveBeenCalled());
    const [message] = addToast.mock.calls.at(-1) as [string, string];
    expect(message).toMatch(/scheduled time for this batch has passed/i);
    expect(message).not.toMatch(/^Retry failed$/i);
  });
});
