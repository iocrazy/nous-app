import { act, cleanup, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import type { CoverFramesMeta } from '../../types';

// vi.mock is hoisted above top-level consts, so the fns it references must be
// hoisted too (vi.hoisted) — otherwise "Cannot access before initialization".
const {
  createPublishTask, promoteGeneratedVideo, uploadResource, getGalleryItems,
  extractCoverFrames, selectCoverFrame, suggestTopics, searchMusic,
  fetchMusicCharts, refreshMusicCharts,
} = vi.hoisted(() => ({
  // 提到 hoisted 而不是在工厂里内联:每个用例都要按自己的场景改它的返回值
  // （冷缓存 / 空榜单 / 读失败），而内联定义在测试里拿不到句柄。
  fetchMusicCharts: vi.fn().mockResolvedValue({
    charts: [], last_success_at: null, stale: true, never_harvested: true, ttl_hours: 24,
  }),
  refreshMusicCharts: vi.fn().mockResolvedValue({ success: true }),
  createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
  promoteGeneratedVideo: vi.fn().mockResolvedValue('900'),
  uploadResource: vi.fn(),
  getGalleryItems: vi.fn().mockResolvedValue([]),
  extractCoverFrames: vi.fn(),
  selectCoverFrame: vi.fn(),
  // Real wire shape of GET /distribution/topics/suggest (CLAUDE.md: boundary
  // mocks copy the backend's JSON, including that view_count is a number and
  // an unseeded topic's id is an empty string — not null, not absent).
  suggestTopics: vi.fn().mockResolvedValue([
    { name: 'goldenhour', topic_id: '1583761434171470', view_count: 30909355369, is_new: false },
    { name: 'goldenhourphotography', topic_id: '', view_count: 0, is_new: true },
  ]),
  // Real wire shape of GET /distribution/music/search (measured 2026-08-15).
  // Two rows with CHARACTER-IDENTICAL titles under different ids, because that
  // is what the platform actually returns — and it is the entire reason the
  // picker exists: a title cannot address either of them.
  //
  // `music_id` is the upstream `id_str`, a STRING. The same upstream row also
  // carries an `id` as a JSON number past 2^53, which is already a different
  // number by the time it reaches a browser; a fixture that "tidied" the two
  // into one numeric id would prove something the real payload never does
  // (CLAUDE.md: boundary mocks copy the wire shape).
  // `duration` is SECONDS — measured 49 / 221 / 323 / 267 in one response.
  //
  // `play_url` is the audition file, and the fixture carries BOTH of its real
  // states because the panel renders a different thing for each and a fixture
  // that only had one would let "renders nothing either way" pass as a result.
  // The two URLs are real captured wire values (see `test/parse.json` and
  // `test/aweme-68.json` in this repo) — plain https objects on the platform's
  // music CDN with no signature or expiry parameters, which is exactly why an
  // audition is possible at all. The third row has none: the backend documents
  // a missing `play_url` as the norm, not the exception.
  searchMusic: vi.fn().mockResolvedValue({
    tracks: [
      { music_id: '6953836671917951012', title: 'Dream It Possible', author: 'Delacey',
        duration: 221, user_count: 30025, cover_url: '',
        play_url: 'https://sf3-cdn-tos.douyinstatic.com/obj/ies-music/6910889805266504461.mp3' },
      { music_id: '7673728791198320674', title: 'Dream It Possible', author: 'Someone Else',
        duration: 195, user_count: 9, cover_url: '',
        play_url: 'https://sf6-cdn-tos.douyinstatic.com/obj/ies-music/7609946018960050970.mp3' },
      { music_id: '7496840954545048329', title: 'Quiet Morning', author: 'No Preview',
        duration: 132, user_count: 4, cover_url: '', play_url: '' },
    ],
    cursor: 20,
    has_more: true,
    cached: false,
    browsing_as: { account_id: '10', username: 'HEYGO', avatar_url: null },
  }),
}));

// Stable toast spy so the upload-failure test can assert on it.
const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock('../../services/distributionService', () => ({
  // 榜单缓存：面板打开时读。默认给"从没采过"——那是一个真实且常见的状态
  // （新账号、刚上线），而且它必须与"读失败"分得开，所以这里不是空数组。
  fetchMusicCharts,
  refreshMusicCharts,
  // 真实实现，不是 stub：页面拿它的返回值当 React key，而「推荐」和「收藏」
  // 在真实面板上共用 category_id='1'——给个只返回 id 的 stub 会让两个 tab 撞 key。
  musicChartKey: (c: { category_kind: string; category_id: string }) =>
    `${c.category_kind}:${c.category_id}`,
  listAccounts: vi.fn().mockResolvedValue([
    // Two bindings of the same platform, because the row copy differs by
    // auth_type: a QR-bound account publishes unattended, an OAuth one needs
    // the user's phone.
    { id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      // Only this one carries an avatar, so the null-avatar rows keep
      // exercising the gradient-tile fallback next to it (D3).
      platform_user_id: 'op1', username: 'HEYGO',
      avatar_url: 'https://p3-pc.douyinpic.com/aweme/100x100/heygo.jpeg',
      auth_type: 'session',
      token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '12', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op3', username: 'OAuth One', avatar_url: null,
      auth_type: 'oauth',
      token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '11', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op2', username: 'Expired One', avatar_url: null,
      auth_type: 'oauth',
      token_expires_at: null, status: 'expired', created_at: '2026-07-08T00:00:00Z' },
  ]),
  // Returns images when the caller asks for mediaType 'image', videos otherwise
  // — mirrors the real backend `types=` filter so content-switch tests are real.
  listLibraryMedia: vi.fn((_scope: string, opts?: { mediaType?: string }) =>
    Promise.resolve(
      opts?.mediaType === 'image'
        ? [
            { id: 'img-1', filename: 'photo-a.jpg', thumbnail_url: null },
            { id: 'img-2', filename: 'photo-b.jpg', thumbnail_url: null },
            { id: 'img-3', filename: 'photo-c.jpg', thumbnail_url: null },
            // A first-class gallery entity row (images mode also requests
            // `types=gallery`). Its own id is never published — picking it
            // expands into child image ids.
            { id: 'gal-1', filename: 'my-gallery', thumbnail_url: null,
              mime_type: 'application/x-mediahub-gallery', gallery_count: 2 },
          ]
        : [{ id: '30', filename: 'clip-a.mp4', thumbnail_url: null }],
    ),
  ),
  listGeneratedVideos: vi.fn().mockResolvedValue([
    { id: '77', name: 'Sunset drone shot', created_at: '2026-07-10T00:00:00Z',
      promoted_resource_id: null },
  ]),
  promoteGeneratedVideo,
  createPublishTask,
  extractCoverFrames,
  selectCoverFrame,
  suggestTopics,
  searchMusic,
  musicSearchFailure: (err: unknown) => ({
    reason: (err as { reason?: string })?.reason ?? null,
    retryAfterS: null,
  }),
  // The page branches on this code to pick its wording; the real one digs the
  // reason out of a DistributionApiError.
  topicSuggestReason: (err: unknown) => (err as { reason?: string })?.reason ?? null,
  // The Images tab is gated on what the BACKEND says each platform can post.
  // The real answer today is video-only — no publisher can drive a gallery yet
  // — so the images coverage in this file (gallery expand, inline upload, pick
  // order, cover copy) runs against a capability response where Douyin CAN.
  //
  // This is also the positive control for the gate: the tab is enabled purely
  // because this response says so, which is what makes the disabled assertions
  // in PublishPage.imagesGate.test.tsx (video-only response, no override) mean
  // something rather than passing on a hardcoded false.
  getPlatformCapabilities: vi.fn().mockResolvedValue({
    douyin: {
      platform: 'douyin',
      supports_publishing: true,
      is_placeholder: false,
      content_types: ['images', 'video'],
      video_extensions: ['.mov', '.mp4', '.webm'],
      image_extensions: ['.jpeg', '.jpg', '.png'],
      min_images: null,
      max_images: null,
      max_title_len: null,
      max_topics: null,
      supports_scheduling: true,
      // The production douyin profile's own numbers: 7800s (the platform's 2h
      // plus a 10-minute upload margin) and 14 days. The page reads the window
      // from here now — a fixture that disagreed with the backend would make
      // the greyed-out days/hours in these tests prove the wrong bound.
      schedule_min_lead_seconds: 7800,
      schedule_max_ahead_seconds: 1209600,
      self_declarations: [],
      supports_collection: true,
      supports_music: true,
    },
  }),
}));

// Tag plumbing behind the "To publish" mark — inert defaults.
vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn().mockResolvedValue({ id: 'tag-1', name: 'To Publish' }),
  addResourceTag: vi.fn().mockResolvedValue(undefined),
  removeResourceTag: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}));

// PublishPage calls useToast — mock it so the test needn't wrap ToastProvider.
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

// Inline image upload path (resourceService.uploadResource) + gallery expand.
vi.mock('../../services/resourceService', () => ({
  uploadResource,
  getGalleryItems,
  getResourceCoverUrl: (id: string) => `/cover/${id}`,
  // Cover frames and the derived crops are plain image resources — the picker
  // renders them through the signed file URL, same as canvas output nodes.
  getResourceFileUrl: (id: string, token?: string) => `/file/${id}?token=${token ?? ''}`,
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

// Realtime double for the cover-frame workflow. Captures the postgres_changes
// handler and the SUBSCRIBED callback so a test can (a) hand the component a
// `task_tracking` row exactly the way Supabase would and (b) exercise the
// seed read that covers the "workflow finished before the socket joined" race.
const { realtime } = vi.hoisted(() => ({
  realtime: {
    updateHandler: null as ((p: { new: unknown }) => void) | null,
    subscribeCb: null as ((s: string) => void) | null,
    seed: { data: null as unknown, error: null as unknown },
    removeChannel: vi.fn(),
    seedSelect: vi.fn(),
  },
}));

vi.mock('../../supabaseClient', () => {
  const channelObj = {
    on: (_evt: string, _cfg: unknown, cb: (p: { new: unknown }) => void) => {
      realtime.updateHandler = cb;
      return channelObj;
    },
    subscribe: (cb: (s: string) => void) => {
      realtime.subscribeCb = cb;
      return channelObj;
    },
  };
  return {
    getSupabaseClient: () => ({
      auth: { getSession: () => Promise.resolve({ data: { session: { access_token: 'jwt' } } }) },
      channel: () => channelObj,
      removeChannel: realtime.removeChannel,
      from: () => ({
        select: (cols: string) => {
          realtime.seedSelect(cols);
          return { eq: () => ({ maybeSingle: () => Promise.resolve(realtime.seed) }) };
        },
      }),
    }),
  };
});

// PublishPage derives scope via useWorkspaceScope → useTeamContext, which throws
// without a TeamProvider. Mock the context so the hook resolves a scope id.
vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId: 'pt1' }),
}));

import PublishPage from './PublishPage';

/** 榜单里一首歌的真实 wire 形状。`user_count` 是三态的 —— 见下方文件注释。 */
type ChartTrack = {
  music_id: string; title: string; author: string; duration: number;
  user_count: number | null; cover_url: string; play_url: string;
};

/* ── 榜单 tab ──────────────────────────────────────────────────────────────

   这个文件里最要紧的一条不是 tab 能不能切,而是 `user_count === null` 不能被
   渲染成 "0 uses"。

   为什么它必须靠测试而不能靠类型:`frontend/tsconfig.json` **没有开
   `strictNullChecks`**(实测:把 `const n: number = null` 放进 `services/` 跑
   `npm run typecheck`,零报错)。所以 `MusicTrack.user_count: number | null`
   这个标注在这个仓库里是文档,不是保证 —— `formatViewCount(n: number)` 收到
   null 编译器一声不吭,然后渲染出一个凭空捏造的观测值。

   0 是真实的目录值(一首没人用过的歌,2026-08-17 那次生产拒绝就是它),null 是
   "平台没说"。把后者显示成前者,就是把"不知道"说成"知道"。
   ────────────────────────────────────────────────────────────────────── */

const chart = (
  kind: string,
  id: string,
  name: string,
  tracks: Array<Partial<ChartTrack>> = [],
  extra: Partial<{ ok: boolean }> = {},
) => ({
  id: `c-${kind}-${id}`,
  category_id: id,
  category_kind: kind,
  category_name: name,
  position: 0,
  ok: extra.ok ?? true,
  error: '',
  cursor: '20',
  has_more: true,
  fetched_at: '2026-08-19T10:00:00Z',
  checked_at: '2026-08-19T10:00:00Z',
  tracks: tracks.map((t, i) => ({
    music_id: t.music_id ?? `id-${kind}-${i}`,
    title: t.title ?? `Track ${i}`,
    author: t.author ?? 'Someone',
    duration: t.duration ?? 30,
    // 默认给一个数,想测 null 的用例自己传 null —— 让"没写"和"故意为 null"
    // 在 fixture 层就分得开。
    user_count: t.user_count === undefined ? 12 : t.user_count,
    cover_url: t.cover_url ?? '',
    play_url: t.play_url ?? '',
  })),
});

/** 一次真实形状的榜单响应。⚠️「推荐」与「收藏」共用 category_id='1'。 */
const chartsPage = (charts: unknown[], over: Record<string, unknown> = {}) => ({
  charts,
  last_success_at: '2026-08-19T10:00:00Z',
  stale: false,
  never_harvested: false,
  ttl_hours: 24,
  ...over,
});

describe('PublishPage music charts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });
  afterEach(cleanup);

  const openPanel = async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.click(screen.getByTestId('music-panel-toggle'));
    return within(await screen.findByTestId('music-panel'));
  };

  it('renders a tab per cached chart and shows the first one on open', async () => {
    fetchMusicCharts.mockResolvedValueOnce(chartsPage([
      chart('recommend', '1', 'Recommended', [{ title: 'Chart One' }]),
      chart('rank', '7088298745502646280', 'Hot', [{ title: 'Chart Two' }]),
    ]));
    const panel = await openPanel();

    await waitFor(() => expect(panel.getByTestId('music-tabs')).toBeInTheDocument());
    expect(panel.getByTestId('music-tab-recommend:1')).toBeInTheDocument();
    expect(panel.getByTestId('music-tab-rank:7088298745502646280')).toBeInTheDocument();
    // 打开就有歌 —— 这正是"打开是空的、得自己想歌名"要解决的那件事。
    expect(panel.getByText('Chart One')).toBeInTheDocument();
    expect(panel.queryByText('Chart Two')).not.toBeInTheDocument();
  });

  it('keys tabs on kind AND id, so 推荐 and 收藏 are two tabs', async () => {
    // **实测的碰撞。** 两个 tab 在平台上都是 category_id='1'。只拿 id 当 key,
    // React 会认为它们是同一个节点,一个会消失 —— 而且不会有任何报错。
    fetchMusicCharts.mockResolvedValueOnce(chartsPage([
      chart('recommend', '1', 'Recommended', [{ title: 'R1' }]),
      chart('fav', '1', 'Saved', [{ title: 'F1' }]),
    ]));
    const panel = await openPanel();

    await waitFor(() => expect(panel.getByTestId('music-tabs')).toBeInTheDocument());
    expect(panel.getByTestId('music-tab-recommend:1')).toBeInTheDocument();
    expect(panel.getByTestId('music-tab-fav:1')).toBeInTheDocument();
    expect(panel.getAllByRole('tab')).toHaveLength(2);
  });

  it('switches the list when another tab is taken', async () => {
    fetchMusicCharts.mockResolvedValueOnce(chartsPage([
      chart('recommend', '1', 'Recommended', [{ title: 'Chart One' }]),
      chart('rank', '9', 'Hot', [{ title: 'Chart Two' }]),
    ]));
    const panel = await openPanel();
    await waitFor(() => expect(panel.getByText('Chart One')).toBeInTheDocument());

    fireEvent.click(panel.getByTestId('music-tab-rank:9'));
    expect(panel.getByText('Chart Two')).toBeInTheDocument();
    expect(panel.queryByText('Chart One')).not.toBeInTheDocument();
  });

  it('renders "we were not told" for a track with no usage count', async () => {
    // **本文件的主要理由。** 见文件头:类型系统在这个仓库里拦不住它。
    fetchMusicCharts.mockResolvedValueOnce(chartsPage([
      chart('recommend', '1', 'Recommended', [
        { title: 'Silent', user_count: null },
        { title: 'Nobody', user_count: 0 },
      ]),
    ]));
    const panel = await openPanel();
    await waitFor(() => expect(panel.getByText('Silent')).toBeInTheDocument());

    const rows = panel.getAllByRole('option');
    // 没说 → 不出现 "uses" 这个词,也不出现任何被当成计数的数字。
    expect(within(rows[0]).queryByText(/uses/i)).not.toBeInTheDocument();
    expect(within(rows[0]).queryByText('0')).not.toBeInTheDocument();
    // 说了 0 → 就照实说 0。这一条是正向对照:少了它,上面那条靠"整块不渲染"
    // 也能过。
    expect(within(rows[1]).getByText(/uses/i)).toBeInTheDocument();
    expect(within(rows[1]).getByText('0')).toBeInTheDocument();
  });

  it('lets a search replace the chart, and going back empty restores it', async () => {
    fetchMusicCharts.mockResolvedValueOnce(chartsPage([
      chart('recommend', '1', 'Recommended', [{ title: 'Chart One' }]),
    ]));
    const panel = await openPanel();
    await waitFor(() => expect(panel.getByText('Chart One')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/^Search music$/i), { target: { value: 'dream' } });
    // getAllByText, not getByText: the search fixture carries TWO rows with
    // character-identical titles under different ids, because that is what the
    // platform really returns — and it is the entire reason this picker exists.
    await waitFor(
      () => expect(panel.getAllByText('Dream It Possible').length).toBe(2),
      { timeout: 3000 },
    );
    // 两个来源**不合并**:搜索答的是"叫这个名字的歌",榜单答的是"平台今天在推
    // 什么",拼在一起哪个都不是——而每一行看起来都是合法的。
    expect(panel.queryByText('Chart One')).not.toBeInTheDocument();
    expect(panel.queryByTestId('music-tabs')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^Search music$/i), { target: { value: '' } });
    await waitFor(() => expect(panel.getByText('Chart One')).toBeInTheDocument());
    expect(panel.getByTestId('music-tabs')).toBeInTheDocument();
  });

  it('tells an empty chart apart from one it could not read', async () => {
    // 实测:没收藏过歌的账号,收藏接口回 137 字节、连 songs 键都没有 —— 那是
    // "读成功了,里面是空的",不是失败。两者合并的后果二选一:把空收藏夹永远
    // 显示成坏了,或者把真的抓取失败显示成"平台上就是没有"。
    fetchMusicCharts.mockResolvedValueOnce(chartsPage([
      chart('fav', '1', 'Saved', []),
      chart('rank', '9', 'Hot', [], { ok: false }),
    ]));
    const panel = await openPanel();

    await waitFor(() => expect(panel.getByTestId('music-chart-empty')).toBeInTheDocument());
    expect(panel.getByTestId('music-chart-empty').textContent).toMatch(/empty on the platform/i);

    fireEvent.click(panel.getByTestId('music-tab-rank:9'));
    expect(panel.getByTestId('music-chart-empty').textContent).toMatch(/could not be read/i);
  });

  it('states what a first harvest costs before offering to run one', async () => {
    // 采一次 = 一次浏览器运行 + 在账号上留一条草稿。用户点之前就该知道,
    // 而不是点完才发现草稿箱多了东西。
    fetchMusicCharts.mockResolvedValueOnce(
      chartsPage([], { never_harvested: true, stale: true, last_success_at: null }),
    );
    const panel = await openPanel();

    const cold = await panel.findByTestId('music-charts-cold');
    expect(cold.textContent).toMatch(/two minutes/i);
    expect(cold.textContent).toMatch(/one draft/i);
    expect(panel.getByTestId('music-charts-refresh')).toBeInTheDocument();
    // 冷缓存不是故障:不该出现 tab 行,也不该出现报错。
    expect(panel.queryByTestId('music-tabs')).not.toBeInTheDocument();
    expect(panel.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('runs a harvest and re-reads the cache when asked', async () => {
    fetchMusicCharts
      .mockResolvedValueOnce(
        chartsPage([], { never_harvested: true, stale: true, last_success_at: null }),
      )
      .mockResolvedValueOnce(chartsPage([
        chart('recommend', '1', 'Recommended', [{ title: 'Fresh One' }]),
      ]));
    const panel = await openPanel();
    fireEvent.click(await panel.findByTestId('music-charts-refresh'));

    await waitFor(() => expect(refreshMusicCharts).toHaveBeenCalledWith('10'));
    await waitFor(() => expect(panel.getByText('Fresh One')).toBeInTheDocument());
  });

  it('says so when the cache itself could not be read', async () => {
    // 读不出缓存是**我们的**问题,不是平台没有榜单。静默空列表会把前者说成
    // 后者(CLAUDE.md「触发路径必须类型化失败回显」)。
    fetchMusicCharts.mockRejectedValueOnce(new Error('boom'));
    const panel = await openPanel();

    const alert = await panel.findByRole('alert');
    expect(alert.textContent).toMatch(/could not read the cached charts/i);
    // 搜索仍然可用,文案要这么说,因为它是真的。
    expect(alert.textContent).toMatch(/search still works/i);
  });
});


describe('music charts — freshness', () => {
  const openPanel = async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.click(screen.getByTestId('music-panel-toggle'));
    return within(await screen.findByTestId('music-panel'));
  };

  it('re-reads a stale cache once, in the background, when the panel opens', async () => {
    refreshMusicCharts.mockClear();
    fetchMusicCharts.mockResolvedValue(chartsPage([chart('recommend', '1', 'Recommended', [{ title: 'Old' }])], {
      stale: true, never_harvested: false, last_success_at: '2026-08-19T10:00:00Z',
    }));
    refreshMusicCharts.mockResolvedValue(undefined);
    await openPanel();
    await waitFor(() => expect(refreshMusicCharts).toHaveBeenCalledTimes(1));
  });

  it('leaves a fresh cache alone, says when it was read, and offers Refresh', async () => {
    refreshMusicCharts.mockClear();
    fetchMusicCharts.mockResolvedValue(chartsPage([chart('recommend', '1', 'Recommended', [{ title: 'New' }])], {
      stale: false, never_harvested: false, last_success_at: '2026-08-19T10:00:00Z',
    }));
    refreshMusicCharts.mockResolvedValue(undefined);
    const panel = await openPanel();
    await waitFor(() => expect(panel.getByTestId('music-fresh')).toBeInTheDocument());
    expect(refreshMusicCharts).not.toHaveBeenCalled();
    expect(panel.getByTestId('music-fresh').textContent).toMatch(/Updated/);
    fireEvent.click(panel.getByTestId('music-refresh'));
    await waitFor(() => expect(refreshMusicCharts).toHaveBeenCalledTimes(1));
  });
});
