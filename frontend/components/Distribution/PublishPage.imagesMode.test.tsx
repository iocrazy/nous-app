/**
 * Image-post mode, from the moment the backend allows it.
 *
 * The sibling PublishPage.imagesGate.test.tsx proves the Images tab stays dead
 * while `/distribution/capabilities` says Douyin can only publish video — which
 * is still the shipped answer today. This file is the other half: it feeds a
 * capability response where Douyin CAN take images and asserts the whole
 * gallery flow works, so the day T7 flips the declaration the page follows with
 * no frontend release. Without this file "it will just work afterwards" is an
 * assertion nobody checked.
 *
 * Two things here are deliberately NOT written as literals:
 *
 *  - the image count bound. Douyin's real maximum is 35 and it is already
 *    declared in three places (the platform page, the backend profile, the
 *    request schema). A fourth copy in the page — or in this test — is exactly
 *    the hand-synced declaration this project exists to delete, so the mocks
 *    use small, arbitrary numbers (4, 2, 5) and the assertions quote whatever
 *    the mock said. A hardcoded 35 in the component would fail every one.
 *
 *  - the refusal copy. The backend sends a typed `reason` plus English prose
 *    written for logs; the page must translate the code, never echo the prose.
 *    Each case below asserts the human sentence appears AND the backend's own
 *    message does not.
 *
 * Rendered under a real i18n instance built from the shipped en.json (unlike
 * the other Publish tests, which run with no instance and therefore see raw
 * `{{n}}` placeholders) — the numbers ARE the point here, so they have to
 * interpolate.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import enJson from '../../public/locales/en.json';
import zhJson from '../../public/locales/zh.json';

const { createPublishTask, listAccounts, getPlatformCapabilities } = vi.hoisted(() => ({
  createPublishTask: vi.fn(),
  listAccounts: vi.fn(),
  getPlatformCapabilities: vi.fn(),
}));

const DOUYIN_ACCOUNT = {
  id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
  auth_type: 'session', token_expires_at: null, status: 'active',
  created_at: '2026-08-11T00:00:00Z',
};

/** A capability response where Douyin takes galleries, with the bounds the
 *  caller wants to test. Everything else mirrors the real response shape. */
const imagesCapableCaps = (over: { min_images?: number | null; max_images?: number | null } = {}) => ({
  douyin: {
    platform: 'douyin',
    supports_publishing: true,
    is_placeholder: false,
    content_types: ['images', 'video'],
    video_extensions: ['.mov', '.mp4', '.webm'],
    image_extensions: ['.jpeg', '.jpg', '.png'],
    min_images: 1,
    max_images: 4,
    max_title_len: null,
    max_topics: null,
    supports_scheduling: true,
    schedule_min_lead_seconds: 7200,
    schedule_max_ahead_seconds: 1209600,
    self_declarations: [],
    supports_collection: true,
    supports_music: true,
    ...over,
  },
});

// The real module is kept underneath the mock: `publishGateProblems` and
// `DistributionApiError` are the code under test on the rejection path, so
// stubbing them would leave the mapping untested and still green.
vi.mock('../../services/distributionService', async (orig) => {
  const actual = await orig<typeof import('../../services/distributionService')>();
  return {
    ...actual,
    listAccounts,
    getPlatformCapabilities,
    createPublishTask,
    listLibraryMedia: vi.fn((_scope: string, opts?: { mediaType?: string }) =>
      Promise.resolve(
        opts?.mediaType === 'image'
          ? [
            { id: 'img-1', filename: 'photo-a.jpg', thumbnail_url: null },
            { id: 'img-2', filename: 'photo-b.jpg', thumbnail_url: null },
            { id: 'img-3', filename: 'photo-c.jpg', thumbnail_url: null },
          ]
          : [{ id: '30', filename: 'clip-a.mp4', thumbnail_url: null }],
      )),
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
  getResourceFileUrl: (id: string) => `/file/${id}`,
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}));

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

vi.mock('../../supabaseClient', () => {
  const channelObj = { on: () => channelObj, subscribe: () => channelObj };
  return {
    getSupabaseAccessToken: () => Promise.resolve('jwt'),
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
import { DistributionApiError } from '../../services/distributionService';

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson }, zh: { translation: zhJson } },
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

const imagesTab = () => screen.getByRole('tab', { name: /^Images$/ });

/** Open the picker, click each named card in order, close it. Pick order IS
 *  gallery order, so the sequence matters. */
const pickImages = async (names: string[]) => {
  fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
  for (const name of names) {
    // Sequential on purpose: the pick order is what the assertion is about.
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(name) }));
  }
  fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
};

const fillAndSubmit = () => {
  fireEvent.click(screen.getByText('HEYGO'));
  fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
    target: { value: 'Gallery day' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
};

/** The 422 envelope `distribution_router.create_task` raises. */
const rejectWith = (reason: string, backendMessage: string, accountId: string | null = '10') =>
  createPublishTask.mockRejectedValueOnce(new DistributionApiError(422, '/tasks', {
    reason: 'publish_intent_rejected',
    message: backendMessage,
    problems: [{ reason, message: backendMessage, account_id: accountId }],
  }));

beforeEach(() => {
  listAccounts.mockResolvedValue([DOUYIN_ACCOUNT]);
  getPlatformCapabilities.mockResolvedValue(imagesCapableCaps());
  createPublishTask.mockReset().mockResolvedValue({ id: '700', accounts: [] });
  addToast.mockClear();
});

describe('PublishPage — image posts, once the backend declares support', () => {
  it('opens the Images tab and carries the whole gallery flow into the payload', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Enabled purely because the capability response says images — nothing in
    // the page knows the word "douyin".
    await waitFor(() => expect(imagesTab()).not.toBeDisabled());
    fireEvent.click(imagesTab());
    expect(imagesTab()).toHaveAttribute('aria-selected', 'true');

    // The shared picker speaks about images here, not videos.
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    expect(screen.getByText('Pick images to include in this post.')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search images')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /photo-b\.jpg/ }));
    fireEvent.click(await screen.findByRole('button', { name: /photo-a\.jpg/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    // The ceiling shown is the one the mock sent (4), not Douyin's real 35.
    expect(screen.getByText('Images · 2 of up to 4 selected')).toBeInTheDocument();

    fillAndSubmit();
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.content_type).toBe('images');
    // Pick order, not library order.
    expect(arg.resource_ids).toEqual(['img-2', 'img-1']);
    expect(arg.distribution_mode).toBe('broadcast');
    // D4: an image post never carries a separate cover asset.
    expect(arg.cover_vertical_resource_id).toBeUndefined();
    expect(arg.cover_horizontal_resource_id).toBeUndefined();
  });

  it('blocks a gallery over the maximum the backend sent, quoting that number', async () => {
    getPlatformCapabilities.mockResolvedValue(imagesCapableCaps({ max_images: 2 }));
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg', 'photo-b.jpg', 'photo-c.jpg']);
    fillAndSubmit();

    // Refused client-side, so nothing was even attempted.
    expect(createPublishTask).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Publish now/i })).toBeDisabled();
    expect(screen.getByText('You picked 3 images — at most 2 fit in one post. Remove 1.'))
      .toBeInTheDocument();
  });

  it('allows the same gallery when the backend raises the maximum', async () => {
    // Same three images, same code, different response — if the bound were a
    // literal in the page, exactly one of these two tests could pass.
    getPlatformCapabilities.mockResolvedValue(imagesCapableCaps({ max_images: 5 }));
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg', 'photo-b.jpg', 'photo-c.jpg']);
    expect(screen.getByText('Images · 3 of up to 5 selected')).toBeInTheDocument();
    fillAndSubmit();

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls.at(-1)?.[0].resource_ids)
      .toEqual(['img-1', 'img-2', 'img-3']);
  });

  it('blocks a gallery under the minimum the backend sent', async () => {
    getPlatformCapabilities.mockResolvedValue(imagesCapableCaps({ min_images: 3, max_images: 9 }));
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg']);
    fillAndSubmit();

    expect(createPublishTask).not.toHaveBeenCalled();
    expect(screen.getByText('An image post needs at least 3 image(s) — you picked 1.'))
      .toBeInTheDocument();
  });

  it('reorders the gallery, with the end buttons disabled rather than inert', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg', 'photo-b.jpg', 'photo-c.jpg']);

    // Nothing is before the first image or after the last one — say so.
    expect(screen.getByRole('button', { name: 'Move photo-a.jpg earlier' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Move photo-c.jpg later' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Move photo-a.jpg later' })).not.toBeDisabled();

    // c moves ahead of b → a, c, b.
    fireEvent.click(screen.getByRole('button', { name: 'Move photo-c.jpg earlier' }));
    // Now c is second and its "earlier" is live while a's is still dead.
    expect(screen.getByRole('button', { name: 'Move photo-b.jpg later' })).toBeDisabled();

    fillAndSubmit();
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls.at(-1)?.[0].resource_ids)
      .toEqual(['img-1', 'img-3', 'img-2']);
  });

  it('offers no separate cover in images mode, and says where the cover comes from', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    expect(screen.getByText(/there is no video to sample/i)).toBeInTheDocument();
    expect(screen.getByText('First image')).toBeInTheDocument();
    // The frame picker is gone entirely — not present-but-disabled, which
    // would still read as "a cover can be attached here".
    expect(screen.queryByRole('button', { name: /Pick a frame from the video/i })).toBeNull();
    expect(screen.queryByText('Vertical 3:4')).toBeNull();
  });
});

describe('PublishPage — the submit-time gate speaks in reasons, not prose', () => {
  /**
   * Each row: the backend `reason`, the log-prose it ships with, and the
   * fragment of the user-facing sentence the page must show instead.
   * The five the spec names, plus the whole-batch (no account) shape.
   */
  const CASES: Array<[string, string, string]> = [
    [
      'too_many_images',
      'too many images (36 > 35)',
      'Too many images — at most 4 fit in one post.',
    ],
    [
      'too_few_images',
      'too few images (0 < 1)',
      'An image post needs at least 1 image(s).',
    ],
    [
      'account_not_session_bound',
      'an image post needs an account connected by QR code (this one would fall back to the H5 share handoff)',
      'Image posts need an account connected by QR code',
    ],
    [
      'cover_not_supported_for_images',
      'an image post takes its cover from the uploaded images; a separate cover asset is not supported',
      'Image posts take their cover from the images themselves',
    ],
    [
      'unsupported_content_type',
      "content_type 'images' not supported on douyin session channel",
      'This account cannot publish this kind of post.',
    ],
    [
      'music_not_supported',
      'music selection is not supported on douyin',
      'cannot have its music picked for it',
    ],
    [
      'invalid_music_name',
      'music name exceeds 100 characters',
      'That music name was refused',
    ],
  ];

  it.each(CASES)('turns %s into copy a user can act on', async (reason, prose, expected) => {
    rejectWith(reason, prose);
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg']);
    fillAndSubmit();

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    // The reason is on screen, attributed to the account that caused it.
    expect(await screen.findByText(new RegExp(`HEYGO — .*${expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`)))
      .toBeInTheDocument();
    // …and the backend's log prose is nowhere near the user.
    expect(screen.queryByText(prose)).toBeNull();
    // Refusals are not toasts that fade: the panel stays while the form is
    // still broken. The toast only points at it.
    expect(addToast).toHaveBeenCalledWith(expect.stringContaining('refused'), 'error');
  });

  it('shows every problem in a mixed batch, not just the first', async () => {
    createPublishTask.mockRejectedValueOnce(new DistributionApiError(422, '/tasks', {
      reason: 'publish_intent_rejected',
      message: 'a; b',
      problems: [
        { reason: 'too_many_images', message: 'a', account_id: '10' },
        { reason: 'title_too_long', message: 'b', account_id: null },
      ],
    }));
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg']);
    fillAndSubmit();

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(await screen.findByText(/Too many images/)).toBeInTheDocument();
    // No account id on this one — it must not be dropped for lack of a name.
    expect(screen.getByText('The title is too long for this platform — shorten it and try again.'))
      .toBeInTheDocument();
  });

  it('drops the refusal once the request it described has changed', async () => {
    rejectWith('too_many_images', 'too many images (36 > 35)');
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg']);
    fillAndSubmit();
    expect(await screen.findByText(/Too many images/)).toBeInTheDocument();

    // Editing the title makes the panel describe a request that no longer
    // exists — leaving it up would blame the user for something they fixed.
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Second attempt' },
    });
    await waitFor(() => expect(screen.queryByText(/Too many images/)).toBeNull());
  });

  it('shows the raw reason code for a reason nobody has written copy for', async () => {
    // A gap to fix, not a gap to hide behind "could not publish".
    rejectWith('brand_new_backend_rule', 'something the frontend has never seen');
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg']);
    fillAndSubmit();

    expect(await screen.findByText(/brand_new_backend_rule/)).toBeInTheDocument();
  });

  it('keeps the generic message when the failure is not a typed refusal', async () => {
    // A 500, or FastAPI's own pydantic 422 (detail is an array, no reason) —
    // inventing a gate verdict for either would describe the wrong failure.
    createPublishTask.mockRejectedValueOnce(new DistributionApiError(422, '/tasks', [
      { loc: ['body', 'resource_ids'], msg: 'at most 35 images allowed', type: 'value_error' },
    ]));
    renderPage();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());
    await pickImages(['photo-a.jpg']);
    fillAndSubmit();

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Could not create publish task', 'error'));
    expect(screen.queryByText(/was refused before anything was created/)).toBeNull();
  });
});

describe('image-post copy is translated in both locales', () => {
  it('has every new key in en.json and zh.json', () => {
    const en = (enJson as Record<string, any>).distribution.publish;
    const zh = (zhJson as Record<string, any>).distribution.publish;
    const added = [
      'imagesSelectedOfMax', 'imagesTooMany', 'imagesTooFew',
      'moveImageEarlier', 'moveImageLater', 'coverFromFirstImage',
      'pickerSubtitleImages', 'pickerSearchImages', 'pickerNoMarkedImages',
      'pickerNoResultsImages', 'allowDownloadsDescImages',
      'rejected', 'rejectedHeading', 'gateProblemForAccount',
      'gateTooManyImages', 'gateTooFewImages', 'gateAccountNotSessionBound',
      'gateCoverNotSupportedForImages', 'gateUnsupportedContentType',
      'gateUnknownReason',
    ];
    for (const key of added) {
      expect(en[key], `en.json missing ${key}`).toBeTruthy();
      expect(zh[key], `zh.json missing ${key}`).toBeTruthy();
    }
    // The whole namespace stays in lockstep — 174 keys each, zero untranslated,
    // and this change must not be the one that breaks it.
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort());
  });
});
