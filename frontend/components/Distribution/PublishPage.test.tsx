import { act, cleanup, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import type { CoverFramesMeta } from '../../types';

// vi.mock is hoisted above top-level consts, so the fns it references must be
// hoisted too (vi.hoisted) — otherwise "Cannot access before initialization".
const {
  createPublishTask, promoteGeneratedVideo, uploadResource, getGalleryItems,
  extractCoverFrames, selectCoverFrame, suggestTopics, searchMusic,
} = vi.hoisted(() => ({
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

describe('PublishPage', () => {
  it('publishes only after content + account + title are chosen', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    // The Library is no longer flooded into the content card — it only appears
    // inside the "Add from Library" picker. Wait for accounts to load first.
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    const publishBtn = screen.getByRole('button', { name: /Publish now/i });
    expect(publishBtn).toBeDisabled();

    // Open the picker, choose a video, then close it. (The tile's accessible
    // name includes the bookmark toggle's label, so match by substring.)
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));                    // pick account
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Launch day' },
    });
    expect(publishBtn).not.toBeDisabled();

    fireEvent.click(publishBtn);
    await waitFor(() => expect(createPublishTask).toHaveBeenCalledTimes(1));
    const arg = createPublishTask.mock.calls[0][0];
    expect(arg.resource_ids).toEqual(['30']);
    expect(arg.account_ids).toEqual(['10']);
    expect(arg.title).toBe('Launch day');
  });

  it('collects topics and submits them in the payload', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Open the topic input via the "# Topic" chip and type two tags (Enter
    // commits each), then run the standard publish flow.
    fireEvent.click(screen.getByRole('button', { name: /# Topic/i }));
    const topicInput = screen.getByLabelText(/Add a topic/i);
    fireEvent.change(topicInput, { target: { value: '#goldenhour' } });
    fireEvent.keyDown(topicInput, { key: 'Enter' });
    fireEvent.change(topicInput, { target: { value: 'cityscape' } });
    fireEvent.keyDown(topicInput, { key: 'Enter' });

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Topic day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    // Leading '#' is stripped; both typed tags are present.
    expect(arg.topics).toEqual(['goldenhour', 'cityscape']);
    // Nothing came from the suggestion list, so there is no entity binding to
    // send — and an empty array must not be invented for one.
    expect(arg.topic_refs).toBeUndefined();
  });

  it('suggests real platform topics while typing and carries the picked id into the payload', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /# Topic/i }));
    fireEvent.change(screen.getByLabelText(/Add a topic/i), { target: { value: 'golden' } });

    // Scoped to the suggestion list on purpose: the page also renders a native
    // <select> for the self declaration, and its <option> elements carry the
    // same ARIA role — an unscoped query would "find" rows that resolve before
    // the lookup has even run.
    const list = await screen.findByTestId('topic-suggest');
    // Debounced: one request for the burst, not one per keystroke.
    const options = await within(list).findAllByRole('option');
    expect(suggestTopics).toHaveBeenCalledWith('golden');
    expect(suggestTopics).toHaveBeenCalledTimes(1);
    const [row, newRow] = options;
    // The play count is shown, compactly, derived from the raw integer.
    expect(row).toHaveTextContent('#goldenhour');
    expect(row).toHaveTextContent('30.9B');
    // A topic the platform does not have yet is labelled, not shown as 0 plays.
    expect(newRow).toHaveTextContent('#goldenhourphotography');
    expect(newRow).toHaveTextContent('New');

    fireEvent.click(row);
    // The chip now carries the platform's entity id — visible proof the pick
    // was captured rather than degraded into plain text.
    await waitFor(() => expect(
      document.querySelector('.chip-topic[data-topic-id="1583761434171470"]'),
    ).not.toBeNull());

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Golden day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.topics).toEqual(['goldenhour']);
    // ⚠️ Reverse-verification target: drop `topic_refs` from the submit payload
    // and this line goes red. The cid only exists at pick time.
    expect(arg.topic_refs).toEqual([
      { name: 'goldenhour', topic_id: '1583761434171470', view_count: 30909355369 },
    ]);
  });

  it('says the lookup failed instead of showing an empty topic list', async () => {
    // The whole point of the typed reason: "no such topic" and "we could not
    // ask" must not both render as an empty dropdown.
    suggestTopics.mockRejectedValueOnce({ reason: 'upstream_unreachable' });
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /# Topic/i }));
    fireEvent.change(screen.getByLabelText(/Add a topic/i), { target: { value: 'golden' } });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Could not reach the topic list/i);
    // And it is NOT the "nothing found" sentence.
    expect(screen.queryByText(/No topics found/i)).toBeNull();
  });

  it('states per account whether publishing needs the user afterwards', async () => {
    // Replaces an older test that asserted the channel picker existed. The
    // picker is gone: a channel only works for accounts bound the matching way,
    // so offering the choice mostly offered a way to pick a broken combination.
    // What the user actually needs to know is per row — whether pressing
    // Publish finishes the job, or leaves them a link to confirm on a phone.
    // That difference is the entire reason someone binds by QR code.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    expect(screen.queryByRole('button', { name: /Official API/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /H5 share/i })).toBeNull();

    expect(screen.getByText(/Publishes unattended/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Needs confirming on your phone/i).length).toBeGreaterThan(0);
  });

  it('draws the real avatar in the account picker, gradient tile for the rest', async () => {
    // The picker drew a deterministic gradient for every row even though the
    // avatar shipped in the same payload (D3).
    const { container } = render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    const img = screen.getByRole('img', { name: 'HEYGO' });
    expect(img).toHaveAttribute('src', 'https://p3-pc.douyinpic.com/aweme/100x100/heygo.jpeg');
    expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');

    expect(container.querySelectorAll('.acct-row .ava').length).toBe(3);
    expect(container.querySelectorAll('.acct-row .ava img').length).toBe(1);
    // Falls back to initials rather than an empty circle.
    expect(screen.getByText('OA')).toBeInTheDocument();
  });

  it('drops back to the gradient tile when the avatar CDN refuses the image', async () => {
    // The failure this guards is invisible without onError: a broken <img>
    // renders the browser's broken-image glyph, it does not disappear.
    const { container } = render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.error(screen.getByRole('img', { name: 'HEYGO' }));

    await waitFor(() => expect(container.querySelectorAll('.acct-row .ava img').length).toBe(0));
    expect(screen.getByText('HE')).toBeInTheDocument();
  });

  it('asks for the session route and lets the backend downgrade per account', async () => {
    // The page always requests 'session'. That is not a claim that every
    // account can do it — `decide_channel` re-decides per account and sends an
    // OAuth-bound one down the H5 handoff. Requesting the weaker 'h5' instead
    // would be the lossy direction: it would strand session accounts on a route
    // that needs a human.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Route check' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls[0][0]).toMatchObject({ channel: 'session' });
  });

  it('promotes a generated video on pick and publishes its resource id', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    // Switch to the Generated tab and pick the generation — first pick
    // promotes it into the Library and selects the resulting resource id.
    fireEvent.click(await screen.findByRole('button', { name: /^Generated$/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Sunset drone shot/ }));
    await waitFor(() => expect(promoteGeneratedVideo).toHaveBeenCalledWith('77'));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Gen launch' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.resource_ids).toEqual(['900']);
  });

  it('marks expired accounts non-selectable', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Expired One')).toBeInTheDocument());
    // The account name ("Expired One") and the status subtitle both match
    // /Expired/i, so assert on the unambiguous reauthorize prompt instead.
    expect(screen.getByText(/reauthorize/i)).toBeInTheDocument();
  });

  it('switches to Images mode and publishes a gallery in pick order', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Flip the content type to Images — the picker now lists image media.
    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    // Generated media is video-only — the tab must be gone in images mode.
    expect(screen.queryByRole('button', { name: /^Generated$/ })).toBeNull();
    // Pick photo-b BEFORE photo-a to prove the gallery order follows the pick
    // order, not the library list order.
    fireEvent.click(await screen.findByRole('button', { name: /photo-b\.jpg/ }));
    fireEvent.click(await screen.findByRole('button', { name: /photo-a\.jpg/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Gallery day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.content_type).toBe('images');
    // Order = pick order (photo-b then photo-a), never the list order.
    expect(arg.resource_ids).toEqual(['img-2', 'img-1']);
    // Images always broadcast — one note per account.
    expect(arg.distribution_mode).toBe('broadcast');
  });

  it('expands a gallery card into its child images in position order', async () => {
    // API returns the children OUT of position order — the picker must sort by
    // position, never trust the response order.
    getGalleryItems.mockResolvedValueOnce([
      { id: 'c-2', filename: 'child-2.jpg', thumbnail_path: null, position: 1 },
      { id: 'c-1', filename: 'child-1.jpg', thumbnail_path: null, position: 0 },
    ]);

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    // The gallery entity renders as its own card (badge shows the child count).
    fireEvent.click(await screen.findByRole('button', { name: /my-gallery/ }));
    await waitFor(() => expect(getGalleryItems).toHaveBeenCalledWith('gal-1'));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Gallery expand' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.content_type).toBe('images');
    // Children are published in POSITION order (c-1 then c-2), not the API's
    // reversed order — and the gallery entity id itself is never published.
    expect(arg.resource_ids).toEqual(['c-1', 'c-2']);
    expect(arg.resource_ids).not.toContain('gal-1');
  });

  it('removes the whole gallery group on a second pick (toggle)', async () => {
    getGalleryItems.mockResolvedValueOnce([
      { id: 'c-1', filename: 'child-1.jpg', thumbnail_path: null, position: 0 },
      { id: 'c-2', filename: 'child-2.jpg', thumbnail_path: null, position: 1 },
    ]);

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    fireEvent.click(screen.getByText('HEYGO'));                       // pick account
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Toggle group' },
    });
    const publishBtn = screen.getByRole('button', { name: /Publish now/i });

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    const galleryCard = await screen.findByRole('button', { name: /my-gallery/ });

    // First pick expands + selects the whole group → publishable.
    fireEvent.click(galleryCard);
    await waitFor(() => expect(getGalleryItems).toHaveBeenCalledWith('gal-1'));
    await waitFor(() => expect(publishBtn).not.toBeDisabled());
    const fetchesAfterExpand = getGalleryItems.mock.calls.length;

    // Second pick removes every child → nothing selected → publish blocked.
    fireEvent.click(galleryCard);
    await waitFor(() => expect(publishBtn).toBeDisabled());
    // Cached children mean the toggle-off never re-fetches.
    expect(getGalleryItems.mock.calls.length).toBe(fetchesAfterExpand);
  });

  it('uploads picked images inline and auto-selects them in pick order', async () => {
    uploadResource.mockReset();
    uploadResource
      .mockResolvedValueOnce({ id: 'up-1', filename: 'up-a.jpg' })
      .mockResolvedValueOnce({ id: 'up-2', filename: 'up-b.jpg' });

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Images mode exposes an enabled Upload button + a hidden file input.
    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    const input = screen.getByLabelText(/Upload images/i);
    const fileA = new File(['a'], 'up-a.jpg', { type: 'image/jpeg' });
    const fileB = new File(['b'], 'up-b.jpg', { type: 'image/jpeg' });
    fireEvent.change(input, { target: { files: [fileA, fileB] } });

    await waitFor(() => expect(uploadResource).toHaveBeenCalledTimes(2));
    // Both uploads auto-selected → their thumbs appear in the content card.
    await waitFor(() => expect(screen.getByLabelText('up-a.jpg')).toBeInTheDocument());
    expect(screen.getByLabelText('up-b.jpg')).toBeInTheDocument();

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Uploaded gallery' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.content_type).toBe('images');
    // Selection order follows the pick order, not upload completion timing.
    expect(arg.resource_ids).toEqual(['up-1', 'up-2']);
  });

  it('shows an error toast when an image upload fails', async () => {
    addToast.mockClear();
    uploadResource.mockReset();
    uploadResource.mockRejectedValueOnce(new Error('boom'));

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    const input = screen.getByLabelText(/Upload images/i);
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'bad.jpg', { type: 'image/jpeg' })] },
    });

    await waitFor(() => expect(uploadResource).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(expect.stringContaining('failed'), 'error'));
  });

  it('clears the selection when toggling content type', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Pick a video — the content card shows a thumb labelled with its filename.
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    expect(screen.getByLabelText('clip-a.mp4')).toBeInTheDocument();

    // Flip to Images — the video selection must reset (thumb gone).
    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    await waitFor(() =>
      expect(screen.queryByLabelText('clip-a.mp4')).toBeNull());
  });
});

// ── Douyin form fields the creator page has and we lacked (mig 407) ──

import { musicPreviewUrl, scheduleProblem } from './PublishPage';

describe('musicPreviewUrl', () => {
  // The panel tests cover the two shapes the catalogue is MEASURED to return
  // (an https object, or nothing at all). This one covers the shape the
  // backend's `_first_url` would also let through — it accepts anything
  // starting with `http` — and that the panel fixture therefore cannot show
  // without inventing wire data we have never seen.
  it('refuses an http:// audition, which the browser would block before we saw an error', () => {
    // app.nous.ink is https. A mixed-content media URL is blocked by the
    // browser BEFORE any `error` event reaches the page, so a play button over
    // one could only ever no-op — worse than no button, because it looks like
    // it should work.
    expect(musicPreviewUrl({ play_url: 'http://sf3-cdn-tos.example.com/obj/ies-music/1.mp3' }))
      .toBeNull();
  });

  it('treats missing, blank and whitespace-only alike — the documented common case', () => {
    expect(musicPreviewUrl({ play_url: '' })).toBeNull();
    expect(musicPreviewUrl({ play_url: '   ' })).toBeNull();
    expect(musicPreviewUrl({ play_url: null })).toBeNull();
    expect(musicPreviewUrl({})).toBeNull();
  });

  it('passes an https URL through untouched — no rewriting, no proxying', () => {
    const real = 'https://sf6-cdn-tos.douyinstatic.com/obj/ies-music/7609946018960050970.mp3';
    expect(musicPreviewUrl({ play_url: real })).toBe(real);
    // Trimmed, because the value is upstream text and a stray newline would
    // otherwise produce a src the CDN answers 404 for.
    expect(musicPreviewUrl({ play_url: `  ${real}\n` })).toBe(real);
  });
});

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

describe('scheduleProblem', () => {
  // The DEFAULT floor is the platform's 2h PLUS a 10 min upload margin — used
  // only until the capabilities response answers (which states the same 7800s).
  // A looser bound here would produce "accepted here, refused there" in the
  // 2h00–2h10 band.
  const now = Date.parse('2026-08-06T12:00:00Z');
  const at = (ms: number) => new Date(now + ms).toISOString();

  it('rejects anything under 2h10m and anything over 14 days', () => {
    expect(scheduleProblem(at(1.98 * HOUR), now)).toBe('tooSoon');
    expect(scheduleProblem(at(2 * HOUR), now)).toBe('tooSoon');
    expect(scheduleProblem(at(2 * HOUR + 5 * 60 * 1000), now)).toBe('tooSoon');
    expect(scheduleProblem(at(2 * HOUR + 10 * 60 * 1000), now)).toBeNull();
    expect(scheduleProblem(at(13 * DAY), now)).toBeNull();
    expect(scheduleProblem(at(14 * DAY), now)).toBeNull();
    expect(scheduleProblem(at(14 * DAY + 60 * 1000), now)).toBe('tooFar');
  });

  it('treats an empty or unparseable value as unfinished, not as valid', () => {
    expect(scheduleProblem('', now)).toBe('empty');
    expect(scheduleProblem('not a date', now)).toBe('empty');
  });

  it('honours a caller-supplied window instead of the module defaults', () => {
    // The page passes the platforms' own numbers (strictest wins across the
    // selected accounts) — the defaults above are only the not-answered-yet
    // fallback, so a bound arriving from the backend has to actually apply.
    const oneHour = HOUR;
    const twoDays = 2 * DAY;
    expect(scheduleProblem(at(90 * 60 * 1000), now, oneHour, twoDays)).toBeNull();
    expect(scheduleProblem(at(30 * 60 * 1000), now, oneHour, twoDays)).toBe('tooSoon');
    expect(scheduleProblem(at(3 * DAY), now, oneHour, twoDays)).toBe('tooFar');
  });
});

describe('PublishPage form fields', () => {
  const pickContentAndAccount = async () => {
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Form fields' },
    });
  };

  it('preselects the AI declaration when the AI toggle goes on, and sends it', async () => {
    // The product decision: `ai_content` existed for two releases and never
    // reached the platform. Flipping it now fills in the matching declaration.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    const select = screen.getByLabelText(/Self declaration/i) as HTMLSelectElement;
    expect(select.value).toBe('');
    fireEvent.click(screen.getByRole('switch', { name: /AI-generated content/i }));
    expect(select.value).toBe('内容由AI生成');

    await pickContentAndAccount();
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.ai_content).toBe(true);
    // The wire value is the platform's own wording — the browser matches this
    // text on the page, so a translated value would select nothing.
    expect(arg.self_declaration).toBe('内容由AI生成');
  });

  it('lets an explicit declaration override the AI toggle and flags the conflict', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('switch', { name: /AI-generated content/i }));
    fireEvent.change(screen.getByLabelText(/Self declaration/i), {
      target: { value: '内容为转载信息' },
    });
    // Disagreeing controls are surfaced, not silently resolved.
    expect(screen.getByText(/declares something else/i)).toBeInTheDocument();

    await pickContentAndAccount();
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls.at(-1)?.[0].self_declaration).toBe('内容为转载信息');
  });

  it('omits the declaration entirely when nothing is chosen', async () => {
    // Omitted ≠ '无需添加自主声明': one leaves the control alone, the other is
    // an explicit declaration the platform records.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickContentAndAccount();
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.self_declaration).toBeUndefined();
    expect(arg.scheduled_at).toBeUndefined();
    expect(arg.collection_name).toBeUndefined();
    // Blank music is OMITTED, not sent as an empty string: "leave the music
    // control alone" (publish on the platform default 原声) is what every post
    // before this field existed did, and it has to keep meaning exactly that.
    expect(arg.music_name).toBeUndefined();
  });

  // ── Schedule picker ──
  //
  // The control here is the app-wide DateTimePopover (the same component the
  // workspace canvas schedules nodes with), NOT `<input type="datetime-local">`.
  // Two things are being pinned, and each has a matching way to break it:
  //   1. it IS the shared popover (put the native input back → the popover
  //      assertions below go red);
  //   2. the platform window is ENFORCED BY DISABLING, not by scolding after
  //      the fact (drop minAt/maxAt, or the hour/day disabled branches → the
  //      `toBeDisabled` assertions below go red).
  //
  // The clock is pinned so "1 hour from now" and "15 days from now" land on a
  // known calendar day and hour — otherwise a run at 23:30 would roll the
  // assertion onto the next day and the test would be flaky rather than wrong.
  describe('schedule picker', () => {
    // 2026-08-06 09:57:30 LOCAL. Window (douyin fixture): +2h10m → 12:07:30
    // today, +14 days → 2026-08-20 09:57:30.
    //
    // Deliberately NOT on a 5-minute boundary: the clock keeps advancing under
    // `shouldAdvanceTime`, so a floor of exactly 12:10:00 would sometimes land
    // a few ms past the 12:10 slot and snap to 12:15 instead. 12:07:30 puts the
    // first legal slot unambiguously at 12:10 with seconds of slack either way.
    const PINNED = new Date(2026, 7, 6, 9, 57, 30, 0);

    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      vi.setSystemTime(PINNED);
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    const openScheduler = async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Schedule$/i }));
      fireEvent.click(screen.getByTestId('publish-schedule-trigger'));
      return screen.getByTestId('date-time-popover');
    };

    it('uses the shared DateTimePopover, not a native datetime-local input', async () => {
      render(<MemoryRouter><PublishPage /></MemoryRouter>);
      await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
      await pickContentAndAccount();
      await openScheduler();

      expect(screen.getByTestId('date-time-popover')).toBeInTheDocument();
      // The time columns are the half `DateRangePopover` used to be missing —
      // "the publish module is just the canvas control plus a time".
      expect(screen.getByTestId('date-time-columns')).toBeInTheDocument();
      expect(document.querySelector('input[type="datetime-local"]')).toBeNull();
    });

    it('greys out every day and hour outside the platform window', async () => {
      render(<MemoryRouter><PublishPage /></MemoryRouter>);
      await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
      await pickContentAndAccount();
      await openScheduler();

      // Yesterday is unreachable, and so is day 15 — both bounds, not just one.
      expect(screen.getByLabelText('2026-08-05')).toBeDisabled();
      expect(screen.getByLabelText('2026-08-21')).toBeDisabled();
      // Inside the window, days are live.
      expect(screen.getByLabelText('2026-08-06')).not.toBeDisabled();
      expect(screen.getByLabelText('2026-08-20')).not.toBeDisabled();

      // Picking today snaps the time forward to the first legal slot (12:10)
      // rather than committing 00:00, and every earlier hour stays dead.
      fireEvent.click(screen.getByLabelText('2026-08-06'));
      expect(screen.getByTestId('date-time-hour-11')).toBeDisabled();
      expect(screen.getByTestId('date-time-hour-12')).not.toBeDisabled();
      expect(screen.getByTestId('date-time-value-cell')).toHaveTextContent('2026-08-06 12:10');
      // 12:05 is inside the selected hour but still under the floor.
      expect(screen.getByTestId('date-time-minute-05')).toBeDisabled();
      expect(screen.getByTestId('date-time-minute-10')).not.toBeDisabled();
    });

    it('blocks publishing until a time is picked, then sends an absolute instant', async () => {
      render(<MemoryRouter><PublishPage /></MemoryRouter>);
      await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
      await pickContentAndAccount();

      const publishBtn = screen.getByRole('button', { name: /Publish now/i });
      expect(publishBtn).not.toBeDisabled();

      // Switching to Schedule with nothing picked must not publish "now".
      fireEvent.click(screen.getByRole('button', { name: /^Schedule$/i }));
      expect(publishBtn).toBeDisabled();
      expect(screen.getByText(/Choose when this should publish/i)).toBeInTheDocument();

      fireEvent.click(screen.getByTestId('publish-schedule-trigger'));
      fireEvent.click(screen.getByLabelText('2026-08-06'));
      fireEvent.click(screen.getByTestId('date-time-hour-16'));
      fireEvent.click(screen.getByTestId('date-time-minute-30'));
      fireEvent.click(screen.getByTestId('date-time-done'));

      expect(publishBtn).not.toBeDisabled();
      fireEvent.click(publishBtn);

      await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
      const arg = createPublishTask.mock.calls.at(-1)?.[0];
      // Sent as an absolute instant — the backend refuses a value with no offset.
      expect(arg.scheduled_at).toMatch(/Z$/);
      expect(new Date(arg.scheduled_at).getTime()).toBe(
        new Date(2026, 7, 6, 16, 30).getTime(),
      );
    });
  });

  it('sends a trimmed collection name and warns about accounts that cannot honour it', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickContentAndAccount();

    fireEvent.change(screen.getByLabelText(/^Collection$/i), {
      target: { value: '  Summer Trip  ' },
    });
    // Only the QR-bound account is selected so far — no warning yet.
    expect(screen.queryByText(/connected by QR code/i)).toBeNull();
    // Adding an OAuth account makes it a real problem: that row will fail
    // rather than publish with the collection dropped.
    fireEvent.click(screen.getByText('OAuth One'));
    expect(screen.getByText(/connected by QR code/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls.at(-1)?.[0].collection_name).toBe('Summer Trip');
  });

  /**
   * Open the music panel, search, and wait for the (debounced) results.
   *
   * Returns the rows scoped to the panel. ⚠️ The topic type-ahead also renders
   * `role="option"` and sits ABOVE this in the DOM, so a page-wide
   * `getAllByRole('option')` silently returns topic suggestions — a click
   * would add a hashtag and the music assertion would fail for a reason that
   * has nothing to do with music.
   */
  const openMusicAndSearch = async (term = 'dream') => {
    fireEvent.click(screen.getByTestId('music-panel-toggle'));
    fireEvent.change(screen.getByLabelText(/^Search music$/i), { target: { value: term } });
    await waitFor(
      () => expect(
        within(screen.getByTestId('music-panel')).getAllByRole('option').length,
      ).toBeGreaterThan(0),
      { timeout: 3000 },
    );
    return within(screen.getByTestId('music-panel')).getAllByRole('option');
  };

  it('publishes the id of the row that was clicked, not its title', async () => {
    // **The guard.** The catalogue returns two rows whose titles are
    // character-identical under different ids — the measured shape. Sending
    // the title would be indistinguishable between them, and the publish step
    // would refuse (`music_ambiguous`) or, before this change, quietly pick
    // the wrong upload. The SECOND row is taken so that "we sent the id of
    // the row that was clicked" cannot pass by accident.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickContentAndAccount();
    const rows = await openMusicAndSearch();
    // Both rows are titled 'Dream It Possible'; the second one is taken so
    // that "the id of the row that was clicked" cannot pass by accident.
    fireEvent.click(rows[1]);

    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const payload = createPublishTask.mock.calls.at(-1)?.[0];
    expect(payload.music_ref.music_id).toBe('7673728791198320674');
    // The fingerprint the browser aligns dialog rows against travels with it —
    // the author and the length are what separate two same-titled uploads.
    expect(payload.music_ref.music_author).toBe('Someone Else');
    expect(payload.music_ref.duration).toBe(195);
    // …and the keyword the browser will type is derived from the pick, never
    // typed separately: two sources of truth here means the platform's own
    // search cannot find what the user chose.
    expect(payload.music_name).toBe('Dream It Possible');
  });

  it('says whose library it is showing', async () => {
    // **The guard.** The browse identity is the first target account, without
    // a control to change it — which the user accepted. What he did not accept
    // is not knowing whose list this is: the platform's saved-tracks tab is
    // account-scoped, so switching target accounts changes the panel silently.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickContentAndAccount();

    fireEvent.click(screen.getByTestId('music-panel-toggle'));
    expect(screen.getByTestId('music-identity')).toHaveTextContent(/HEYGO/);
  });

  it('asks the platform only while the panel is open', async () => {
    // Every lookup mints a search credential with a REAL account's cookies, so
    // a request the user did not ask for is a risk-control signal spent for
    // nothing — and the publish page is opened far more often than music is
    // chosen (the field has been empty on every publish so far).
    //
    // ⚠️ Asserting "no call before the panel opens" ALONE proves nothing: with
    // no keyword typed there is nothing to search for either way, so it passes
    // whether or not the guard exists. The falsifiable half is the second one:
    // a keyword survives the panel closing, and without the guard the next
    // re-render searches again with it.
    //
    // Cleared before the render — the calls in question include the render's own.
    searchMusic.mockClear();
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickContentAndAccount();

    expect(searchMusic).not.toHaveBeenCalled();

    const rows = await openMusicAndSearch();
    expect(searchMusic).toHaveBeenCalledTimes(1);

    // Picking closes the panel; the typed keyword is still in state.
    fireEvent.click(rows[0]);
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Another title, another render' },
    });
    // Long enough that a debounced lookup would have fired by now.
    await new Promise((resolve) => { setTimeout(resolve, 700); });

    expect(searchMusic).toHaveBeenCalledTimes(1);
  });

  it('shows a typed reason instead of an empty list when the lookup fails', async () => {
    // "The platform has no such track" and "we could not ask" are different
    // sentences. An empty panel for the second is the failure this repo keeps
    // paying for — and here it would be a lie twice over, since a nonsense
    // keyword still returns fuzzy matches upstream.
    searchMusic.mockRejectedValueOnce({ reason: 'session_unusable' });
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickContentAndAccount();

    fireEvent.click(screen.getByTestId('music-panel-toggle'));
    fireEvent.change(screen.getByLabelText(/^Search music$/i), { target: { value: 'dream' } });

    await waitFor(
      () => expect(screen.getByTestId('music-error')).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(screen.getByTestId('music-error')).toHaveTextContent(/needs reconnecting/i);
    expect(
      within(screen.getByTestId('music-panel')).queryAllByRole('option'),
    ).toHaveLength(0);
  });

  it('warns about accounts that cannot honour a chosen track', async () => {
    // Music is set by clicking through the creator page, so an OAuth account
    // cannot honour it — the same group as the collection and the schedule.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickContentAndAccount();
    const rows = await openMusicAndSearch();
    fireEvent.click(rows[0]);

    expect(screen.queryByText(/connected by QR code/i)).toBeNull();
    fireEvent.click(screen.getByText('OAuth One'));
    expect(screen.getByText(/connected by QR code/i)).toBeInTheDocument();
  });

  // ==================================================================
  // Auditioning a track.
  //
  // `play_url` has been on the wire since the panel shipped and nothing read
  // it, so "can I hear it first?" had no answer. Every test below drives the
  // real panel — a hook tested on its own would pass just as happily wired to
  // nothing.
  //
  // jsdom implements neither `play()` nor `pause()`, so both are spied. That
  // makes "was it asked to play" and "was it asked to stop" the observable
  // facts, and each test clears the spy immediately before the action it is
  // about so a pause from somewhere earlier cannot stand in for the one being
  // asserted.
  // ==================================================================
  describe('music audition', () => {
    const PREVIEWABLE = '6953836671917951012';
    const OTHER = '7673728791198320674';
    const NO_PREVIEW = '7496840954545048329';
    const URL_A = 'https://sf3-cdn-tos.douyinstatic.com/obj/ies-music/6910889805266504461.mp3';
    const URL_B = 'https://sf6-cdn-tos.douyinstatic.com/obj/ies-music/7609946018960050970.mp3';

    let playSpy: ReturnType<typeof vi.spyOn>;
    let pauseSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      playSpy = vi
        .spyOn(HTMLMediaElement.prototype, 'play')
        .mockImplementation(() => Promise.resolve());
      pauseSpy = vi
        .spyOn(HTMLMediaElement.prototype, 'pause')
        .mockImplementation(() => undefined);
    });

    afterEach(() => {
      // Unmount BEFORE the stubs come off. Testing Library's auto-cleanup runs
      // after this hook, and the unmount pauses a still-playing element — with
      // the spy already restored that reaches jsdom's unimplemented `pause()`
      // and prints a page of noise that has nothing to do with any assertion.
      cleanup();
      playSpy.mockRestore();
      pauseSpy.mockRestore();
    });

    const audio = (): HTMLAudioElement =>
      screen.getByTestId('music-preview-audio') as HTMLAudioElement;

    const openPanel = async () => {
      render(<MemoryRouter><PublishPage /></MemoryRouter>);
      await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
      await pickContentAndAccount();
      return openMusicAndSearch();
    };

    it('offers a control only on rows the catalogue can actually play', async () => {
      // Both halves matter. The "no button" half alone would pass on a page
      // with no audition at all — the pressable one on the row that HAS a
      // file is what makes this test about the feature.
      await openPanel();
      expect(screen.getByTestId(`music-preview-${PREVIEWABLE}`).tagName).toBe('BUTTON');
      expect(screen.getByTestId(`music-preview-${OTHER}`).tagName).toBe('BUTTON');
      expect(screen.queryByTestId(`music-preview-${NO_PREVIEW}`)).toBeNull();
      // …and the row without one says so, rather than going silently bare.
      const none = screen.getByTestId(`music-preview-none-${NO_PREVIEW}`);
      expect(none.tagName).not.toBe('BUTTON');
      expect(none).toHaveTextContent(/no preview/i);
    });

    it('plays the URL of the row that was pressed', async () => {
      await openPanel();
      fireEvent.click(screen.getByTestId(`music-preview-${OTHER}`));
      // The SECOND row's URL, so "it played something" cannot pass for "it
      // played the right thing" — the same guard the id-not-title test uses.
      expect(audio().src).toBe(URL_B);
      expect(playSpy).toHaveBeenCalled();
      expect(screen.getByTestId(`music-preview-${OTHER}`)).toHaveAttribute('aria-pressed', 'true');
    });

    it('stops the first track when a second one is started', async () => {
      await openPanel();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      expect(audio().src).toBe(URL_A);

      pauseSpy.mockClear();
      fireEvent.click(screen.getByTestId(`music-preview-${OTHER}`));
      expect(pauseSpy).toHaveBeenCalled();
      expect(audio().src).toBe(URL_B);
      // Exactly one row reads as sounding. Two elements (or an untracked id)
      // would leave both pressed, which is the audible bug in DOM form.
      expect(screen.getByTestId(`music-preview-${PREVIEWABLE}`)).toHaveAttribute('aria-pressed', 'false');
      expect(screen.getByTestId(`music-preview-${OTHER}`)).toHaveAttribute('aria-pressed', 'true');
    });

    it('pressing the control again stops it', async () => {
      await openPanel();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      pauseSpy.mockClear();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      expect(pauseSpy).toHaveBeenCalled();
      expect(screen.getByTestId(`music-preview-${PREVIEWABLE}`)).toHaveAttribute('aria-pressed', 'false');
    });

    it('auditioning a track is not choosing it', async () => {
      // The control sits inside the row that selects, so without a stopped
      // click the play button would pick the song AND close the panel — a
      // press that does something the user did not ask for.
      await openPanel();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      expect(screen.getByTestId('music-panel')).toBeInTheDocument();
      expect(screen.queryByTestId('music-chosen')).toBeNull();
    });

    it('stops when the panel is closed', async () => {
      // Sound the user can no longer see the source of is sound the user
      // cannot stop.
      await openPanel();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      pauseSpy.mockClear();
      fireEvent.click(screen.getByTestId('music-panel-toggle'));
      expect(screen.queryByTestId('music-panel')).toBeNull();
      expect(pauseSpy).toHaveBeenCalled();
    });

    it('stops when the search is retyped', async () => {
      await openPanel();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      pauseSpy.mockClear();
      fireEvent.change(screen.getByLabelText(/^Search music$/i), {
        target: { value: 'something else' },
      });
      expect(pauseSpy).toHaveBeenCalled();
    });

    it('stops when the page unmounts', async () => {
      // The one that pins the never-nulled ref: React detaches refs before
      // passive cleanups run, so a cleanup reading a plain ref finds null and
      // pauses nothing — and the audio outlives the page.
      render(<MemoryRouter><PublishPage /></MemoryRouter>);
      await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
      await pickContentAndAccount();
      await openMusicAndSearch();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));

      pauseSpy.mockClear();
      cleanup();
      expect(pauseSpy).toHaveBeenCalled();
    });

    it('says so when the file will not load, instead of going quiet', async () => {
      await openPanel();
      fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      expect(screen.queryByTestId('music-preview-error')).toBeNull();

      fireEvent.error(audio());
      const note = await screen.findByTestId('music-preview-error');
      expect(note).toHaveTextContent(/would not play/i);
      expect(note).toHaveAttribute('role', 'alert');
      expect(screen.getByTestId(`music-preview-${PREVIEWABLE}`)).toHaveAttribute('aria-pressed', 'false');
    });

    it('says so when play() itself is refused', async () => {
      // Autoplay policy and decode failures reject the promise rather than
      // firing `error`. An unhandled rejection is a press that produces
      // nothing at all.
      playSpy.mockImplementation(() => Promise.reject(new Error('NotAllowedError')));
      await openPanel();
      await act(async () => {
        fireEvent.click(screen.getByTestId(`music-preview-${PREVIEWABLE}`));
      });
      const note = await screen.findByTestId('music-preview-error');
      expect(note).toHaveTextContent(/would not play/i);
      expect(screen.getByTestId(`music-preview-${PREVIEWABLE}`)).toHaveAttribute('aria-pressed', 'false');
    });

    it('picking a track still works with the control in the row', async () => {
      // The row became a div to hold a second control; this is the check that
      // it did not stop being selectable on the way.
      const rows = await openPanel();
      fireEvent.click(rows[1]);
      fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
      await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
      expect(createPublishTask.mock.calls.at(-1)?.[0].music_ref.music_id).toBe(OTHER);
    });

    it('a row is still selectable from the keyboard', async () => {
      const rows = await openPanel();
      fireEvent.keyDown(rows[1], { key: 'Enter' });
      expect(screen.getByTestId('music-chosen')).toHaveTextContent('Dream It Possible');
      expect(screen.queryByTestId('music-panel')).toBeNull();
    });
  });
});

describe('PublishPage cover from video frames', () => {
  beforeEach(() => {
    extractCoverFrames.mockReset().mockResolvedValue({ task_id: 'wf-1' });
    selectCoverFrame.mockReset().mockResolvedValue({
      cover_vertical_resource_id: 'cv-1',
      cover_horizontal_resource_id: 'ch-1',
    });
    createPublishTask.mockClear();
    realtime.updateHandler = null;
    realtime.subscribeCb = null;
    realtime.seed = { data: null, error: null };
    realtime.seedSelect.mockClear();
  });

  /** Select the one library video, the way every other flow here does. */
  const pickVideo = async () => {
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
  };

  /** Deliver a `metadata.cover_frames` patch the way the workflow writes it.
   *  Waits for the subscription first — the effect that installs the handler
   *  runs a tick after the POST resolves. */
  const pushFrames = async (
    coverFrames: Partial<CoverFramesMeta>,
    phase = 'in_progress',
  ) => {
    await waitFor(() => expect(realtime.updateHandler).not.toBeNull());
    await act(async () => {
      realtime.updateHandler?.({
        new: {
          phase,
          metadata: {
            cover_frames: { source_resource_id: '30', candidates: [], ...coverFrames },
          },
        },
      });
    });
  };

  /** The candidate tiles, in the order the workflow produced them.
   *  Queried positionally rather than by accessible name: i18n interpolation
   *  is inert under test, so every tile's label would read "Frame at {{time}}".
   *  The timestamps ARE asserted — as the visible text they render as. */
  const frameTiles = () => screen.findAllByRole('radio');

  const CANDIDATES = [
    { resource_id: 'f-1', timestamp_seconds: 0, width: 1080, height: 1920, filename: 'f1.jpg' },
    { resource_id: 'f-2', timestamp_seconds: 72, width: 1080, height: 1920, filename: 'f2.jpg' },
  ];

  it('says a video is needed before offering to sample frames', async () => {
    // The old placeholder was a dimmed "Soon" slot; the failure mode to avoid
    // now is a live-looking button that cannot possibly work.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    expect(screen.getByText(/Pick a video above first/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Pick a frame from the video/i })).toBeNull();
    expect(extractCoverFrames).not.toHaveBeenCalled();

    await pickVideo();
    expect(screen.queryByText(/Pick a video above first/i)).toBeNull();
    expect(screen.getByRole('button', { name: /Pick a frame from the video/i })).toBeInTheDocument();
  });

  it('requests extraction and renders the candidates Realtime pushes', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickVideo();

    fireEvent.click(screen.getByRole('button', { name: /Pick a frame from the video/i }));
    await waitFor(() =>
      expect(extractCoverFrames).toHaveBeenCalledWith({ resource_id: '30' }));
    // Waiting is visible — not a button that quietly did nothing.
    expect(await screen.findByText(/Sampling frames/i)).toBeInTheDocument();

    // The subscription must filter on the DBOS workflow id and seed itself on
    // SUBSCRIBED: a short video can finish before the socket joins, and that
    // UPDATE is then gone for good.
    await waitFor(() => expect(realtime.subscribeCb).not.toBeNull());
    await act(async () => { realtime.subscribeCb?.('SUBSCRIBED'); });
    expect(realtime.seedSelect).toHaveBeenCalledWith('metadata, phase');

    await pushFrames({ candidates: CANDIDATES }, 'completed');
    expect(await frameTiles()).toHaveLength(2);
    expect(screen.getByText('0:00')).toBeInTheDocument();
    expect(screen.getByText('1:12')).toBeInTheDocument();
    expect(screen.queryByText(/Sampling frames/i)).toBeNull();
  });

  it('seeds candidates written before the socket joined', async () => {
    // The race the seed read exists for: the row already carries the finished
    // candidate list, and no UPDATE will ever arrive.
    realtime.seed = {
      data: { phase: 'completed', metadata: { cover_frames: { source_resource_id: '30', candidates: CANDIDATES } } },
      error: null,
    };
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickVideo();

    fireEvent.click(screen.getByRole('button', { name: /Pick a frame from the video/i }));
    await waitFor(() => expect(realtime.subscribeCb).not.toBeNull());
    await act(async () => { realtime.subscribeCb?.('SUBSCRIBED'); });

    expect(await frameTiles()).toHaveLength(2);
    expect(screen.getByText('1:12')).toBeInTheDocument();
  });

  it('derives both crops on pick and carries them into the publish payload', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickVideo();

    fireEvent.click(screen.getByRole('button', { name: /Pick a frame from the video/i }));
    await waitFor(() => expect(extractCoverFrames).toHaveBeenCalled());
    await pushFrames({ candidates: CANDIDATES }, 'completed');

    fireEvent.click((await frameTiles())[1]);
    // No publish_task_id: the task does not exist yet, so the ids ride along
    // in the create body instead.
    await waitFor(() =>
      expect(selectCoverFrame).toHaveBeenCalledWith({ frame_resource_id: 'f-2' }));
    expect(await screen.findByText(/Cover set/i)).toBeInTheDocument();

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Cover day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.cover_vertical_resource_id).toBe('cv-1');
    expect(arg.cover_horizontal_resource_id).toBe('ch-1');
  });

  it('omits the cover ids when no frame was chosen', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickVideo();

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'No cover' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.cover_vertical_resource_id).toBeUndefined();
    expect(arg.cover_horizontal_resource_id).toBeUndefined();
    // Not an error — but the user should know the platform will choose.
    expect(screen.getByText(/the platform picks a frame during publish/i)).toBeInTheDocument();
  });

  it('shows the workflow failure reason and offers a retry', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickVideo();

    fireEvent.click(screen.getByRole('button', { name: /Pick a frame from the video/i }));
    await waitFor(() => expect(extractCoverFrames).toHaveBeenCalledTimes(1));
    // A 504 is the retryable shape — the server-authored detail is the only
    // place the specific reason appears, so it has to reach the screen.
    await pushFrames(
      { error: 'frame extraction timed out', error_status: 504 },
      'failed',
    );

    expect(await screen.findByText(/frame extraction timed out/i)).toBeInTheDocument();
    expect(screen.getByText(/Sampling took too long/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Try again/i }));
    await waitFor(() => expect(extractCoverFrames).toHaveBeenCalledTimes(2));
  });

  it('does not offer a retry when the source itself cannot be sampled', async () => {
    // A 422 from `extract` never becomes true by asking again — a retry button
    // there just farms clicks on a problem the user cannot solve this way.
    extractCoverFrames.mockRejectedValueOnce(
      Object.assign(new Error('unprocessable'), { status: 422 }),
    );
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickVideo();

    fireEvent.click(screen.getByRole('button', { name: /Pick a frame from the video/i }));
    expect(await screen.findByText(/Frames can only be sampled from a video file/i))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Try again/i })).toBeNull();
  });

  it('reports a failed crop instead of silently keeping no cover', async () => {
    selectCoverFrame.mockRejectedValueOnce(
      Object.assign(new Error('boom'), { status: 500 }),
    );
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await pickVideo();

    fireEvent.click(screen.getByRole('button', { name: /Pick a frame from the video/i }));
    await waitFor(() => expect(extractCoverFrames).toHaveBeenCalled());
    await pushFrames({ candidates: CANDIDATES }, 'completed');
    fireEvent.click((await frameTiles())[0]);

    expect(await screen.findByText(/Could not build the covers from that frame/i))
      .toBeInTheDocument();
    // The summary must still say no cover is attached — the failure cannot
    // leave the page claiming one is.
    expect(screen.getByText(/the platform picks a frame during publish/i)).toBeInTheDocument();
  });

  it('explains that image posts have no video to sample', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    expect(await screen.findByText(/there is no video to sample/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Pick a frame from the video/i })).toBeNull();
  });
});

/**
 * Workspace attribution on the publish payload.
 *
 * The page has always known its workspace — `useWorkspaceScope().scopeId` is
 * what it lists Library media with — but it never told the backend which
 * workspace the BATCH belonged to. So `publish_tasks.team_id` was NULL on every
 * row, the issue mirrored from it inherited no team, and the To-do list (whose
 * every scope carries a `team_id` filter) AND-ed those issues away: a real user
 * published twice, both failed, and the blocked work items were invisible.
 *
 * These two cases pin the scope the page actually submits, in both workspaces.
 */
describe('PublishPage workspace attribution', () => {
  const publishOnce = async () => {
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Attribution day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    return createPublishTask.mock.calls.at(-1)?.[0];
  };

  it('submits the personal team id when publishing from the personal workspace', async () => {
    // No `/team/:teamId` in the URL — the personal workspace. It is NOT
    // team-less: the personal workspace IS a team row (`teams.kind='personal'`),
    // which is exactly why sending its id makes the To-do filter match.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);

    // ⚠️ Reverse-verification target: drop `team_id` from the submit payload
    // and this line goes red.
    expect((await publishOnce()).team_id).toBe('pt1');
  });

  it('submits the URL team id when publishing from a real team workspace', async () => {
    // Positive control for the line above: proves the value tracks the ACTIVE
    // workspace rather than being hardcoded to the personal team.
    render(
      <MemoryRouter initialEntries={['/team/990088776655/distribution/publish']}>
        <Routes>
          <Route path="/team/:teamId/distribution/publish" element={<PublishPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect((await publishOnce()).team_id).toBe('990088776655');
  });
});
