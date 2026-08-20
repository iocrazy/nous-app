/**
 * The Images tab has to say WHICH of four things is true — and only one of them
 * is a statement about the platform.
 *
 * Reported from production: the page said *"Image posts are not supported yet —
 * the connected platforms can only publish video"* while the whole chain
 * underneath said the opposite (the browser profile lists images, the endpoint
 * returns `['images','video']`, CORS lets it through). The bug was never in the
 * answer; it was that "still loading", "our request failed" and "the platform
 * said no" all collapsed into `?? false` and were then rendered as an assertion
 * about the platform.
 *
 * What must NOT change is the safe default: without evidence the tab stays
 * disabled. `getPlatformCapabilities` is documented as pure server-side
 * constants, so a rejection means OUR call failed and is no reason to assume
 * support. Every test below re-asserts that the tab is still dead — the fix is
 * to the copy, not to the gate.
 *
 * Falsifiability note: each state asserts on a string that does not exist
 * anywhere in the pre-fix page AND on the absence of the old "not supported
 * yet" sentence, so none of these can pass against the old collapsed gate.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const { listAccounts, getPlatformCapabilities } = vi.hoisted(() => ({
  listAccounts: vi.fn(),
  getPlatformCapabilities: vi.fn(),
}));

/** A live session-bound Douyin account — the shape the real endpoint returns. */
const DOUYIN_ACCOUNT = {
  id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
  auth_type: 'session', token_expires_at: null, status: 'active',
  created_at: '2026-08-08T00:00:00Z',
};

const CAP = {
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
};

const VIDEO_ONLY = { douyin: { ...CAP, content_types: ['video'] } };
const IMAGES_OK = { douyin: { ...CAP, content_types: ['images', 'video'] } };

beforeEach(() => {
  listAccounts.mockResolvedValue([DOUYIN_ACCOUNT]);
  getPlatformCapabilities.mockReset();
  getPlatformCapabilities.mockResolvedValue(VIDEO_ONLY);
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
  listLibraryMedia: vi.fn().mockResolvedValue([
    { id: '30', filename: 'clip-a.mp4', thumbnail_url: null },
  ]),
  listGeneratedVideos: vi.fn().mockResolvedValue([]),
  promoteGeneratedVideo: vi.fn(),
  createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
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

const imagesTab = () => screen.getByRole('tab', { name: /^Images$/ });
const NOT_SUPPORTED = /Image posts are not supported yet/i;

/** Wait until the account list has painted, i.e. the page is past first load. */
const rendered = async () => {
  render(<MemoryRouter><PublishPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
};

describe('PublishPage — "we have not looked yet" is not "the platform said no"', () => {
  it('says it is still checking while the lookup is in flight', async () => {
    // Never settles: the page stays in the state the user was actually shown a
    // platform verdict in. On the pre-fix page this window rendered the
    // "not supported yet" sentence, so the absence assertion below is what
    // makes this test red against it.
    getPlatformCapabilities.mockReturnValue(new Promise(() => {}));
    await rendered();

    expect(screen.getByText(/Checking what the connected platforms can publish/i))
      .toBeInTheDocument();
    expect(screen.queryByText(NOT_SUPPORTED)).toBeNull();
    // Safe default intact: no evidence still means no image posts.
    expect(imagesTab()).toBeDisabled();
    expect(imagesTab()).toHaveAttribute('aria-busy', 'true');
  });

  it('blames our own lookup, not the platform, when the request fails', async () => {
    getPlatformCapabilities.mockRejectedValue(new Error('network'));
    await rendered();

    expect(await screen.findByText(/could not read what the connected platforms can publish/i))
      .toBeInTheDocument();
    // The distinguishing half: a failed request must not be reported as a
    // platform limitation.
    expect(screen.queryByText(NOT_SUPPORTED)).toBeNull();
    expect(imagesTab()).toBeDisabled();
    // Not "busy" — we finished looking and failed.
    expect(imagesTab()).toHaveAttribute('aria-busy', 'false');
  });

  it('offers a retry that actually re-asks and can open the tab', async () => {
    // "Retryable" in the copy has to be backed by a control, otherwise it is a
    // politer dead end.
    getPlatformCapabilities.mockRejectedValueOnce(new Error('network'));
    getPlatformCapabilities.mockResolvedValue(IMAGES_OK);
    await rendered();

    const retry = await screen.findByRole('button', { name: /Check again/i });
    expect(getPlatformCapabilities).toHaveBeenCalledTimes(1);

    fireEvent.click(retry);

    await waitFor(() => expect(imagesTab()).not.toBeDisabled());
    expect(getPlatformCapabilities).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/could not read what the connected platforms/i)).toBeNull();
    expect(screen.queryByText(NOT_SUPPORTED)).toBeNull();
  });

  it('still says "not supported" when the platform really did answer no', async () => {
    // The counterweight to everything above: the honest refusal must remain
    // reachable, or the fix would have traded one wrong sentence for another.
    getPlatformCapabilities.mockResolvedValue(VIDEO_ONLY);
    await rendered();

    expect(await screen.findByText(NOT_SUPPORTED)).toBeInTheDocument();
    expect(screen.queryByText(/could not read what the connected platforms/i)).toBeNull();
    expect(screen.queryByText(/Checking what the connected platforms/i)).toBeNull();
    expect(imagesTab()).toBeDisabled();
  });

  it('treats a response with no entry for the platform as no answer, not a no', async () => {
    // Third way the old `?? false` lied: the request succeeded but carries no
    // record for this account's platform. That is still zero evidence about
    // what the platform can do.
    getPlatformCapabilities.mockResolvedValue({ kuaishou: { ...CAP, platform: 'kuaishou' } });
    await rendered();

    expect(await screen.findByText(/could not read what the connected platforms can publish/i))
      .toBeInTheDocument();
    expect(screen.queryByText(NOT_SUPPORTED)).toBeNull();
    expect(imagesTab()).toBeDisabled();
  });

  it('never enters images mode from an unresolved lookup, however the click arrives', async () => {
    // The gate value is what arms the post; the `disabled` attribute is only a
    // rendering detail. Both unresolved states must keep the page on Video.
    getPlatformCapabilities.mockRejectedValue(new Error('network'));
    await rendered();
    await screen.findByText(/could not read what the connected platforms can publish/i);

    fireEvent.click(imagesTab());

    expect(screen.getByRole('tab', { name: /^Video$/ })).toHaveAttribute('aria-selected', 'true');
    expect(imagesTab()).toHaveAttribute('aria-selected', 'false');
  });

  it('says why the music field is missing when the lookup failed', async () => {
    // Same defect, quieter shape: `supports_music ?? false` hides the row, and
    // a row that vanishes for want of an answer is indistinguishable from one
    // the platform genuinely cannot offer.
    getPlatformCapabilities.mockRejectedValue(new Error('network'));
    await rendered();

    expect(await screen.findByText(/Music is hidden because we could not read/i))
      .toBeInTheDocument();
    expect(screen.queryByLabelText(/^Music$/)).toBeNull();
  });

  it('says nothing about music when the platform simply has none', async () => {
    // The mirror image — an answered "no" needs no apology, and printing one
    // would be the same category error in reverse.
    getPlatformCapabilities.mockResolvedValue({
      douyin: { ...CAP, supports_music: false },
    });
    await rendered();

    await screen.findByText(NOT_SUPPORTED);
    expect(screen.queryByText(/Music is hidden because we could not read/i)).toBeNull();
  });
});
