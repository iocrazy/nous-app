/**
 * The Images tab, against what the BACKEND says Douyin can publish today.
 *
 * Backstory: three layers claimed image posts and disagreed. The page offered
 * an "Images" tab, the backend profile listed `images` in `content_types`, and
 * the browser service — the only layer that actually posts — refused it with
 * `unsupported_content_type`. So the user picked images, uploaded them, filled
 * in the title, submitted, waited in the queue, and only then found out.
 *
 * The refusal was right; its timing was the defect. These tests assert the
 * user-visible half of moving it to the front: with a Douyin account connected,
 * the tab is dead before anything is filled in, and it says why — and "why" is
 * now two different sentences, because "you have not connected an account" and
 * "the platform you connected can't do this" used to share one, which told a
 * user with no accounts to go wait for a feature.
 *
 * The page holds no capability constants at all any more; everything here is
 * driven by the `getPlatformCapabilities` response. The sibling file
 * PublishPage.test.tsx feeds it a response where Douyin CAN take images — that
 * keeps the gallery/upload coverage alive and proves this gate follows the
 * response rather than being hardcoded off.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const { createPublishTask, listAccounts, getPlatformCapabilities } = vi.hoisted(() => ({
  createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
  listAccounts: vi.fn(),
  getPlatformCapabilities: vi.fn(),
}));

const DOUYIN_ACCOUNT = {
  id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
  auth_type: 'session', token_expires_at: null, status: 'active',
  created_at: '2026-08-08T00:00:00Z',
};

/** The shape the real endpoint returns for Douyin today: video only. */
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
  listAccounts.mockResolvedValue([DOUYIN_ACCOUNT]);
  getPlatformCapabilities.mockResolvedValue(VIDEO_ONLY);
  createPublishTask.mockClear();
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
  listLibraryMedia: vi.fn((_scope: string, opts?: { mediaType?: string }) =>
    Promise.resolve(
      opts?.mediaType === 'image'
        ? [{ id: 'img-1', filename: 'photo-a.jpg', thumbnail_url: null }]
        : [{ id: '30', filename: 'clip-a.mp4', thumbnail_url: null }],
    ),
  ),
  listGeneratedVideos: vi.fn().mockResolvedValue([]),
  promoteGeneratedVideo: vi.fn(),
  createPublishTask,
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
  const channelObj = {
    on: () => channelObj,
    subscribe: () => channelObj,
  };
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

describe('PublishPage — image posts are refused up front, not at the last step', () => {
  it('disables the Images tab for a Douyin account and says why', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    expect(imagesTab()).toBeDisabled();
    // A greyed-out control with no explanation reads as a bug. The reason is
    // on the card, not only in a title attribute nobody hovers.
    expect(screen.getByText(/Image posts are not supported yet/i)).toBeInTheDocument();
    // Video is still the live choice — this gate removes a claim, not a page.
    expect(screen.getByRole('tab', { name: /^Video$/ })).not.toBeDisabled();
  });

  it('stays on Video when the disabled tab is clicked anyway', async () => {
    // fireEvent.click on a disabled button is a no-op in the DOM, so this also
    // covers the second lock inside onContentTypeChange: whichever way the
    // click arrives, the page must not enter images mode.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());

    expect(screen.getByRole('tab', { name: /^Video$/ })).toHaveAttribute('aria-selected', 'true');
    expect(imagesTab()).toHaveAttribute('aria-selected', 'false');
    // The content card still counts videos rather than images — i.e. the
    // load() effect never switched to mediaType 'image'. (The count itself is
    // an i18n placeholder here: no i18n instance is initialised in tests, so
    // t() returns the raw default string.)
    expect(screen.getByText(/^Video ·/)).toBeInTheDocument();
    expect(screen.queryByText(/^Images ·/)).toBeNull();
  });

  it('says "connect an account" when there are none — not "not supported yet"', async () => {
    // The old gate collapsed both cases into the unsupported copy, so a user
    // with zero accounts was told to wait for a feature when what they needed
    // was the Accounts page. Two states, two sentences.
    listAccounts.mockResolvedValue([]);
    render(<MemoryRouter><PublishPage /></MemoryRouter>);

    await waitFor(() => expect(imagesTab()).toBeDisabled());
    expect(await screen.findByText(/Connect an account before publishing an image post/i))
      .toBeInTheDocument();
    expect(screen.queryByText(/Image posts are not supported yet/i)).toBeNull();
  });

  it('un-greys itself when the backend starts allowing images — no code change', async () => {
    // The whole point of moving the capability table server-side: the day the
    // browser service can drive a gallery, this response changes and the tab
    // opens. If this ever needs a frontend edit to pass, the claim "no frontend
    // release needed" (spec T7 acceptance #1) is false.
    getPlatformCapabilities.mockResolvedValue({
      douyin: { ...VIDEO_ONLY.douyin, content_types: ['images', 'video'] },
    });
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    await waitFor(() => expect(imagesTab()).not.toBeDisabled());
    expect(screen.queryByText(/Image posts are not supported yet/i)).toBeNull();

    fireEvent.click(imagesTab());
    expect(imagesTab()).toHaveAttribute('aria-selected', 'true');
  });

  it('keeps the tab disabled when the capabilities request fails', async () => {
    // A failed request is not evidence that the platform supports galleries.
    // Failing open here would re-arm the exact late refusal this gate removes.
    getPlatformCapabilities.mockRejectedValue(new Error('network'));
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    expect(imagesTab()).toBeDisabled();
  });

  it('still publishes video end to end', async () => {
    // The gate must not cost anything the user could actually do today.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Launch day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls.at(-1)?.[0]).toMatchObject({ content_type: 'video' });
  });
});
