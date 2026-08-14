/**
 * The publish picker's identifying metadata + inline preview.
 *
 * The picker used to show a thumbnail and a filename and nothing else, which
 * cannot answer the one question it exists to answer once a library holds
 * several cuts of the same piece: *which one is this?* Two exports of the same
 * edit routinely share a filename stem and differ only in length, dimensions,
 * weight or date.
 *
 * Two properties are worth a test, and they pull in opposite directions:
 *
 *   1. **Present values are shown, formatted, and distinguishable.** Two rows
 *      whose filenames are near-identical must render visibly different
 *      metadata — that is the entire feature. Asserting one row's text in
 *      isolation would pass even if every row rendered the same string.
 *   2. **Absent values are shown as an em dash, never invented.** Real coverage
 *      is high but not total (of 204 production videos: 203 have a duration,
 *      201 a resolution, 200 a thumbnail), and the generated-media tab carries
 *      none of it. A fabricated "0:00" or a size back-computed from bitrate
 *      would be worse than a blank: the user would pick the wrong cut and have
 *      no way to notice.
 *
 * Fixture shapes follow the wire, per CLAUDE.md's 边界 mock 纪律: `listLibraryMedia`
 * is the frontend service (already normalised), so its documented return type
 * — numbers for `duration_seconds`/`file_size_bytes`, ISO string for
 * `created_at` — is what belongs here.
 */
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { vi, describe, it, expect } from 'vitest';

import enJson from '../../public/locales/en.json';

const { listAccounts } = vi.hoisted(() => ({ listAccounts: vi.fn() }));

const DOUYIN_ACCOUNT = {
  id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
  auth_type: 'session', token_expires_at: null, status: 'active',
  created_at: '2026-08-11T00:00:00Z',
};

/**
 * Three cuts of the same piece — the exact situation the user described
 * ("万一有好几个版本呢?"). Same stem, so filename alone is useless; every
 * distinguishing signal is in the metadata.
 *
 * The third row has NOTHING but a filename. That is not a contrived edge case:
 * it is what the generated-media tab and older library rows actually look like.
 */
// Inside `vi.hoisted` because `vi.mock` factories are hoisted above module
// scope: a plain top-level const would still be in its temporal dead zone when
// the factory runs.
const { VIDEOS } = vi.hoisted(() => ({ VIDEOS: [
  {
    id: '30',
    filename: 'launch-cut.mp4',
    thumbnail_url: null,
    duration_seconds: 154,
    resolution: '1080x1920',
    file_size_bytes: 19364154,
    created_at: '2026-08-11T05:23:28.889912Z',
  },
  {
    id: '31',
    filename: 'launch-cut-v2.mp4',
    thumbnail_url: null,
    // Over an hour, so the H:MM:SS branch of the duration formatter is real
    // rather than theoretical, and a landscape master against a vertical one.
    duration_seconds: 3616,
    resolution: '1920x1080',
    file_size_bytes: 550133695,
    created_at: '2026-08-12T01:12:52.437118Z',
  },
  {
    id: '32',
    filename: 'launch-cut-old.mp4',
    thumbnail_url: null,
    duration_seconds: null,
    resolution: null,
    file_size_bytes: null,
    created_at: null,
  },
] }));

vi.mock('../../services/distributionService', async (orig) => {
  const actual = await orig<typeof import('../../services/distributionService')>();
  return {
    ...actual,
    listAccounts,
    getPlatformCapabilities: vi.fn().mockResolvedValue({}),
    createPublishTask: vi.fn(),
    listLibraryMedia: vi.fn().mockResolvedValue(VIDEOS),
    listGeneratedVideos: vi.fn().mockResolvedValue([]),
    promoteGeneratedVideo: vi.fn(),
    extractCoverFrames: vi.fn(),
    selectCoverFrame: vi.fn(),
  };
});

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
  getResourceFileUrl: (id: string, token?: string) =>
    `/file/${id}${token ? `?token=${token}` : ''}`,
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
    getSupabaseAccessToken: () => Promise.resolve('jwt'),
    getSupabaseClient: () => ({
      auth: {
        getSession: () =>
          Promise.resolve({ data: { session: { access_token: 'jwt' } } }),
      },
      channel: () => channelObj,
      removeChannel: vi.fn(),
      from: () => ({
        select: () => ({
          eq: () => ({ maybeSingle: () => Promise.resolve({ data: null, error: null }) }),
        }),
      }),
    }),
  };
});

vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId: 'pt1' }),
}));

import PublishPage from './PublishPage';

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
};

const renderPage = () => render(
  <I18nextProvider i18n={makeI18n()}>
    <MemoryRouter><PublishPage /></MemoryRouter>
  </I18nextProvider>,
);

/** Open the library picker and wait for the cards. */
const openPicker = async () => {
  listAccounts.mockResolvedValue([DOUYIN_ACCOUNT]);
  const utils = renderPage();
  await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
  await screen.findByRole('button', { name: /launch-cut\.mp4/ });
  return utils;
};

/** The card element for a video, found via its filename label. */
const cardFor = (filename: string): HTMLElement => {
  const label = screen.getByText(filename);
  const card = label.closest('.picker-item');
  if (!card) throw new Error(`no picker card for ${filename}`);
  return card as HTMLElement;
};

describe('publish picker — telling versions apart', () => {
  it('shows duration, resolution, size and date for each video', async () => {
    await openPicker();

    const first = cardFor('launch-cut.mp4');
    expect(within(first).getByText('2:34')).toBeInTheDocument();       // 154s
    expect(within(first).getByText('1080×1920')).toBeInTheDocument();
    expect(within(first).getByText('18.5 MB')).toBeInTheDocument();
    // Matched by pattern, not by literal: the date is rendered in the viewer's
    // local zone, so a fixed string would encode the test machine's TZ and
    // break in CI. The property that matters is "a real date rendered", which
    // the em-dash test below pins from the other side.
    expect(within(first).getByText(/^Aug \d{1,2}, 2026$/)).toBeInTheDocument();
  });

  it('renders two cuts of the same piece with visibly different metadata', async () => {
    // The point of the feature: same stem, and the numbers are what separate
    // them. Asserting a single card in isolation would still pass if every
    // card rendered identical text, so both are compared here.
    await openPicker();

    const v1 = cardFor('launch-cut.mp4');
    const v2 = cardFor('launch-cut-v2.mp4');

    expect(within(v1).getByText('2:34')).toBeInTheDocument();
    expect(within(v2).getByText('1:00:16')).toBeInTheDocument();       // 3616s
    expect(within(v1).getByText('1080×1920')).toBeInTheDocument();
    expect(within(v2).getByText('1920×1080')).toBeInTheDocument();
    // 524.64 MB → "525 MB": the formatter drops the decimal at ≥100, where a
    // tenth of a megabyte is noise rather than a distinguishing signal.
    expect(within(v1).getByText('18.5 MB')).toBeInTheDocument();
    expect(within(v2).getByText('525 MB')).toBeInTheDocument();
  });

  it('shows an em dash for every field it does not have, and invents nothing', async () => {
    await openPicker();

    const bare = cardFor('launch-cut-old.mp4');
    // Duration badge + all three metadata slots fall back, and none of them
    // borrows a neighbour's value.
    expect(within(bare).getAllByText('—')).toHaveLength(4);
    expect(within(bare).queryByText('2:34')).not.toBeInTheDocument();
    expect(within(bare).queryByText(/MB/)).not.toBeInTheDocument();
    expect(within(bare).queryByText('0:00')).not.toBeInTheDocument();
    expect(within(bare).queryByText('0 B')).not.toBeInTheDocument();
  });

  it('plays the video inline when preview is pressed, and only one at a time', async () => {
    const { container } = await openPicker();

    expect(container.querySelector('video')).toBeNull();

    const previewButtons = screen.getAllByRole('button', { name: /Preview video/i });
    fireEvent.click(previewButtons[0]);

    const video = container.querySelector('video');
    expect(video).not.toBeNull();
    // Authenticated transport: a media element cannot send a header, so the
    // JWT rides as ?token= (the same path CoverPicker uses for its frames).
    expect(video).toHaveAttribute('src', '/file/30?token=jwt');
    // Never autoplay — a grid that starts making noise by itself is a worse
    // default than one extra click.
    expect(video).not.toHaveAttribute('autoplay');

    // Opening another preview must replace the first, not add a second decoder.
    fireEvent.click(screen.getAllByRole('button', { name: /Preview video/i })[0]);
    expect(container.querySelectorAll('video')).toHaveLength(1);
  });

  it('previewing a video does not select it', async () => {
    // The preview control sits inside the card's own click target. Without
    // stopPropagation, wanting a closer look would silently add the clip to
    // the publish — the user would ship a video they were only inspecting.
    const { container } = await openPicker();

    fireEvent.click(screen.getAllByRole('button', { name: /Preview video/i })[0]);

    expect(cardFor('launch-cut.mp4')).not.toHaveClass('sel');
    expect(container.querySelectorAll('.picker-item.sel')).toHaveLength(0);
  });

  it('closes the preview and restores the card', async () => {
    const { container } = await openPicker();

    fireEvent.click(screen.getAllByRole('button', { name: /Preview video/i })[0]);
    expect(container.querySelector('video')).not.toBeNull();

    fireEvent.click(screen.getByText('Close preview'));

    // Torn down, not merely hidden: a <video> left mounted keeps buffering
    // after the user has said they are done with it.
    expect(container.querySelector('video')).toBeNull();
  });

  it('Escape closes the preview before it closes the picker', async () => {
    // Backing out of a preview must not also discard the search and scroll
    // position the user built up to find that video.
    const { container } = await openPicker();

    fireEvent.click(screen.getAllByRole('button', { name: /Preview video/i })[0]);
    expect(container.querySelector('video')).not.toBeNull();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(container.querySelector('video')).toBeNull();
    expect(screen.getByText('launch-cut.mp4')).toBeInTheDocument();     // still open

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() =>
      expect(screen.queryByText('launch-cut.mp4')).not.toBeInTheDocument());
  });
});
