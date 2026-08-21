/**
 * Both dead statuses lock a row on the Publish page — not just the OAuth one.
 *
 * Reported from production. An account that cannot publish right now says so in
 * one of two ways: `expired` is an OAuth token that lapsed, `needs_relogin` is
 * a dead browser session. This page checked `status === 'expired'` alone, while
 * AccountsPage had covered both since it was written. A QR-bound (session)
 * account NEVER reaches `expired` — when it dies it dies as `needs_relogin` —
 * so for anyone publishing unattended (the whole point of QR binding) the guard
 * was checking a status their accounts cannot have. The row stayed live, the
 * user ticked it, pressed Publish, and learned about it from a failed batch.
 *
 * It survived because the fixtures had the same hole: `PublishPage.test.tsx`
 * carries `'active'` and `'expired'` and nothing else, so no test ever ran the
 * branch that was broken. Every case below is built on a `needs_relogin`
 * account for exactly that reason.
 *
 * The two recoveries differ (reauthorize at the platform vs scan a new QR
 * code), so the copy differs too — and it borrows AccountsPage's words, since
 * that page is where the user is being sent.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const { listAccounts, getPlatformCapabilities, createPublishTask } = vi.hoisted(() => ({
  listAccounts: vi.fn(),
  getPlatformCapabilities: vi.fn(),
  createPublishTask: vi.fn(),
}));

const base = {
  scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  avatar_url: null, token_expires_at: null, created_at: '2026-08-08T00:00:00Z',
};

/**
 * The account mix the reporting user actually has: session bindings, which is
 * why `expired` alone never fired. The OAuth row is kept so the previously
 * covered branch stays covered.
 */
const ACCOUNTS = [
  { ...base, id: '10', platform_user_id: 'op1', username: 'Live Session', auth_type: 'session', status: 'active' },
  { ...base, id: '11', platform_user_id: 'op2', username: 'Dead Session', auth_type: 'session', status: 'needs_relogin' },
  { ...base, id: '12', platform_user_id: 'op3', username: 'Lapsed Token', auth_type: 'oauth', status: 'expired' },
];

const VIDEO_ONLY = {
  douyin: {
    platform: 'douyin',
    supports_publishing: true,
    is_placeholder: false,
    content_types: ['video'],
    video_extensions: ['.mov', '.mp4', '.webm'],
    image_extensions: ['.jpeg', '.jpg', '.png'],
    min_images: null,
    max_images: null,
    max_title_len: null,
    max_topics: null,
    supports_scheduling: true,
    schedule_min_lead_seconds: 7200,
    schedule_max_ahead_seconds: 1209600,
    self_declarations: [],
    supports_collection: true,
    supports_music: true,
  },
};

beforeEach(() => {
  listAccounts.mockResolvedValue(ACCOUNTS);
  getPlatformCapabilities.mockResolvedValue(VIDEO_ONLY);
  createPublishTask.mockReset();
  createPublishTask.mockResolvedValue({ id: '700', accounts: [] });
});

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
  listAccounts,
  getPlatformCapabilities,
  createPublishTask,
  listLibraryMedia: vi.fn().mockResolvedValue([
    { id: '30', filename: 'clip-a.mp4', thumbnail_url: null },
  ]),
  listGeneratedVideos: vi.fn().mockResolvedValue([]),
  promoteGeneratedVideo: vi.fn(),
  extractCoverFrames: vi.fn(),
  selectCoverFrame: vi.fn(),
}));

vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn().mockResolvedValue({ id: 'tag-1', name: 'To Publish' }),
  addResourceTag: vi.fn().mockResolvedValue(undefined),
  removeResourceTag: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../services/resourceService', () => ({
  uploadResource: vi.fn(),
  getGalleryItems: vi.fn().mockResolvedValue([]),
  getResourceCoverUrl: (id: string) => `/cover/${id}`,
  getResourceFileUrl: (id: string) => `/file/${id}`,
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

vi.mock('../../supabaseClient', () => {
  const channelObj = { on: () => channelObj, subscribe: () => channelObj };
  return {
    getSupabaseClient: () => ({
      auth: { getSession: () => Promise.resolve({ data: { session: { access_token: 'jwt' } } }) },
      channel: () => channelObj,
      removeChannel: vi.fn(),
      from: () => ({
        select: () => ({ eq: () => ({ maybeSingle: () => Promise.resolve({ data: null, error: null }) }) }),
      }),
    }),
  };
});

vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId: 'pt1' }),
}));

import PublishPage from './PublishPage';

/** The account row is the checkbox whose accessible name carries the handle. */
const row = (name: RegExp) => screen.getByRole('checkbox', { name });

const rendered = async () => {
  render(<MemoryRouter><PublishPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText('Live Session')).toBeInTheDocument());
};

describe('PublishPage — a dead browser session is as unusable as a lapsed token', () => {
  it('locks the needs_relogin row', async () => {
    await rendered();
    // Pre-fix this attribute was "false": `status === 'expired'` is false for a
    // dead session, so the row rendered fully live.
    expect(row(/Dead Session/)).toHaveAttribute('aria-disabled', 'true');
    expect(row(/Dead Session/)).toHaveAttribute('tabindex', '-1');
  });

  it('refuses to select a dead session however the user tries', async () => {
    await rendered();
    const dead = row(/Dead Session/);

    fireEvent.click(dead);
    fireEvent.keyDown(dead, { key: 'Enter' });
    fireEvent.keyDown(dead, { key: ' ' });

    // The interaction guard, not just the attribute: pre-fix each of these
    // toggled the account into the selection.
    expect(row(/Dead Session/)).toHaveAttribute('aria-checked', 'false');
    // Nothing else got selected either — a stray toggle would be just as wrong.
    screen.getAllByRole('checkbox').forEach((el) => {
      expect(el).toHaveAttribute('aria-checked', 'false');
    });
  });

  it('tells a dead session to scan again, and a lapsed token to reauthorize', async () => {
    await rendered();
    // Two recoveries, two sentences — the same vocabulary AccountsPage uses on
    // the card the user is about to be sent to. Pre-fix the dead session row
    // showed its workspace scope here and no status at all.
    expect(screen.getByText(/Signed out — scan again/)).toBeInTheDocument();
    expect(screen.getByText(/Expired — reauthorize/)).toBeInTheDocument();
  });

  it('hides per-account customization for both dead statuses', async () => {
    await rendered();
    // Three accounts, one usable: exactly one Customize control.
    expect(screen.getAllByRole('button', { name: /Customize/ })).toHaveLength(1);
  });

  it('keeps the live session fully usable', async () => {
    // The guard must cost nothing to a healthy account — otherwise "fail
    // closed" would have quietly become "fail on everything".
    await rendered();
    fireEvent.click(row(/Live Session/));

    expect(row(/Live Session/)).toHaveAttribute('aria-checked', 'true');
    expect(row(/Live Session/)).toHaveAttribute('aria-disabled', 'false');
    // …and it is the only one, so "usable" did not leak to the dead rows.
    expect(row(/Dead Session/)).toHaveAttribute('aria-checked', 'false');
    expect(row(/Lapsed Token/)).toHaveAttribute('aria-checked', 'false');
  });

  it('never lets a dead session reach the publish payload', async () => {
    // The end the user actually felt: the batch was created against an offline
    // account and failed later. `account_ids` is the last place to catch it.
    await rendered();

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(row(/Live Session/));
    fireEvent.click(row(/Dead Session/));
    fireEvent.click(row(/Lapsed Token/));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Launch day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls.at(-1)?.[0].account_ids).toEqual(['10']);
  });
});
