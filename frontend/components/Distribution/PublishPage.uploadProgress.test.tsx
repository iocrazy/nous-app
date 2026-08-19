/**
 * Inline image upload: the progress read-out, the rate, the cancel, and the
 * typed failure list.
 *
 * ⚠️ WHAT WENT WRONG, AND WHY IT NEEDED A TEST RATHER THAN A FIX.
 *
 * `uploadResource` has reported upload progress since it was written. This
 * page simply never passed the callback, so the user got a spinner and nothing
 * else — no bytes, no rate, no way out. Nothing failed; a capability existed
 * and its only caller declined to use it, silently. The first test below is
 * therefore about the WIRING, not about the read-out: a page that renders a
 * beautiful panel from numbers nobody feeds it is exactly the state this was
 * already in.
 *
 * ⚠️ EVERY ASSERTION IS POSITIVE, and the read-out ones are equalities on
 * exact strings. "The panel does not show a wrong speed" is satisfied by a
 * panel that never rendered; `toBe('≈ 1.0 MB/s')` is not.
 *
 * Rendered under a real i18n instance built from the shipped en.json, because
 * the interpolated numbers ARE the thing under test.
 */
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import enJson from '../../public/locales/en.json';
import zhJson from '../../public/locales/zh.json';

const { listAccounts, getPlatformCapabilities, uploadResource } = vi.hoisted(() => ({
  listAccounts: vi.fn(),
  getPlatformCapabilities: vi.fn(),
  uploadResource: vi.fn(),
}));

const DOUYIN_ACCOUNT = {
  id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
  auth_type: 'session', token_expires_at: null, status: 'active',
  created_at: '2026-08-11T00:00:00Z',
};

const imagesCapableCaps = () => ({
  douyin: {
    platform: 'douyin',
    supports_publishing: true,
    is_placeholder: false,
    content_types: ['images', 'video'],
    video_extensions: ['.mov', '.mp4', '.webm'],
    image_extensions: ['.jpeg', '.jpg', '.png'],
    min_images: 1,
    max_images: 9,
    max_title_len: null,
    max_topics: null,
    supports_scheduling: true,
    schedule_min_lead_seconds: 7200,
    schedule_max_ahead_seconds: 1209600,
    self_declarations: [],
    supports_collection: true,
    supports_music: true,
  },
});

vi.mock('../../services/distributionService', async (orig) => {
  const actual = await orig<typeof import('../../services/distributionService')>();
  return {
    ...actual,
    listAccounts,
    getPlatformCapabilities,
    createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
    listLibraryMedia: vi.fn().mockResolvedValue([]),
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
  uploadResource,
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

import PublishPage, {
  UPLOAD_RATE_MIN_GAP_MS,
  UPLOAD_RATE_SMOOTHING,
  describeUploadFailure,
  emptyUploadRate,
  foldUploadRate,
} from './PublishPage';

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

const MIB = 1024 * 1024;

/** A File with a REAL byte length, because the denominator is `file.size`. */
const bigFile = (name: string, bytes: number) =>
  new File([new Uint8Array(bytes)], name, { type: 'image/jpeg' });

/**
 * A rejection shaped exactly like `ResourceUploadError`.
 *
 * ⚠️ Hand-built rather than imported because this file mocks the whole of
 * `resourceService`. The shape is not guessed: `resourceService.upload.test.ts`
 * asserts `name` / `reason` / `status` / `detail` on the real class, and
 * `name === 'ResourceUploadError'` is the exact discriminant
 * `describeUploadFailure` keys on — so the two sides are pinned from both ends
 * rather than agreeing by luck.
 */
const uploadRejection = (reason: string, detail: string | null) => {
  const err = new Error(detail ?? `Upload failed (${reason})`);
  err.name = 'ResourceUploadError';
  Object.assign(err, { reason, status: null, detail });
  return err;
};

beforeEach(() => {
  listAccounts.mockResolvedValue([DOUYIN_ACCOUNT]);
  getPlatformCapabilities.mockResolvedValue(imagesCapableCaps());
  uploadResource.mockReset();
  addToast.mockClear();
});

/** Get into Images mode and hand the hidden input a set of files. */
const pickFiles = async (files: File[]) => {
  renderPage();
  await waitFor(() => { expect(screen.getByText('HEYGO')).toBeInTheDocument(); });
  fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
  fireEvent.change(screen.getByLabelText(/Upload images/i), { target: { files } });
};

/**
 * ══ THE RATE, AS ARITHMETIC ═════════════════════════════════════════════════
 *
 * Tested as a pure function rather than only through the page, because the
 * smoothing is the part most likely to be quietly wrong and it is invisible
 * from the outside: a page showing a plausible-looking number is exactly what
 * a broken estimator produces.
 */
describe('foldUploadRate', () => {
  /** One point has no rate. Reporting one from a single sample would be
   *  reporting a measurement that has not been taken. */
  it('treats the first sample as an anchor, not a reading', () => {
    const state = foldUploadRate(emptyUploadRate(), { loaded: 1_000, at: 0 });
    expect(state.bytesPerSecond).toBe(null);
    expect(state.last).toEqual({ loaded: 1_000, at: 0 });
  });

  /**
   * The browser delivers progress in bursts as socket buffers drain, so two
   * samples a few milliseconds apart divide by a denominator small enough that
   * ordinary burstiness decides the answer. Such a pair is not a reading.
   */
  it('ignores a sample too close to the last one to be a reading', () => {
    const anchored = foldUploadRate(emptyUploadRate(), { loaded: 1_000, at: 0 });
    const next = foldUploadRate(anchored, {
      loaded: 9_999_999,
      at: UPLOAD_RATE_MIN_GAP_MS - 1,
    });
    expect(next).toBe(anchored);
  });

  it('measures the first real reading exactly', () => {
    const anchored = foldUploadRate(emptyUploadRate(), { loaded: 0, at: 0 });
    const measured = foldUploadRate(anchored, { loaded: 2 * MIB, at: 1_000 });
    expect(measured.bytesPerSecond).toBe(2 * MIB);
    expect(measured.last).toEqual({ loaded: 2 * MIB, at: 1_000 });
  });

  /** The smoothing itself, as a number rather than as "it looks steadier". */
  it('folds later readings in with the declared weight', () => {
    let state = foldUploadRate(emptyUploadRate(), { loaded: 0, at: 0 });
    state = foldUploadRate(state, { loaded: 1_000_000, at: 1_000 });
    expect(state.bytesPerSecond).toBe(1_000_000);

    // Second reading: 3 MB in one second. Smoothed, not adopted whole.
    state = foldUploadRate(state, { loaded: 4_000_000, at: 2_000 });
    expect(state.bytesPerSecond)
      .toBe(1_000_000 + (3_000_000 - 1_000_000) * UPLOAD_RATE_SMOOTHING);
  });

  /**
   * The aggregate can go backwards when a file drops out of the batch. Folding
   * that in would print a negative rate — a speed the connection never ran at.
   * Re-anchor and keep the last real reading instead.
   */
  it('re-anchors instead of folding in a backwards jump', () => {
    let state = foldUploadRate(emptyUploadRate(), { loaded: 0, at: 0 });
    state = foldUploadRate(state, { loaded: 1_000_000, at: 1_000 });
    const back = foldUploadRate(state, { loaded: 400_000, at: 2_000 });

    expect(back.bytesPerSecond).toBe(1_000_000);
    expect(back.last).toEqual({ loaded: 400_000, at: 2_000 });
  });
});

/**
 * A throw we did not classify is not evidence of a dropped connection. Calling
 * it one would be inventing a cause — the same defect as inventing a number,
 * in prose.
 */
describe('describeUploadFailure', () => {
  it('reads a typed upload error’s own reason and detail', () => {
    expect(describeUploadFailure('a.jpg', uploadRejection('too_large', 'Max 500 MB.')))
      .toEqual({ name: 'a.jpg', reason: 'too_large', detail: 'Max 500 MB.' });
  });

  it('calls an unclassified throw unknown rather than guessing a cause', () => {
    expect(describeUploadFailure('a.jpg', new Error('boom')))
      .toEqual({ name: 'a.jpg', reason: 'unknown', detail: null });
    expect(describeUploadFailure('a.jpg', 'a string, somehow'))
      .toEqual({ name: 'a.jpg', reason: 'unknown', detail: null });
  });
});

describe('the upload read-out', () => {
  /**
   * ⚠️ THE ORIGINAL DEFECT. Everything else in this file is downstream of it.
   *
   * The service could always report progress; the page never asked. So this
   * asserts the ARGUMENTS, positionally, rather than anything on screen — a
   * panel wired to nothing renders just as prettily.
   */
  it('asks the service for progress, and hands it something to cancel with', async () => {
    uploadResource.mockReturnValue(new Promise(() => {}));
    await pickFiles([bigFile('one.jpg', 4 * MIB)]);

    await waitFor(() => { expect(uploadResource).toHaveBeenCalledTimes(1); });
    const args = uploadResource.mock.calls[0];
    expect(typeof args[3]).toBe('function');
    expect(args[5]).toBeInstanceOf(AbortSignal);
    expect((args[5] as AbortSignal).aborted).toBe(false);
  });

  /**
   * The whole read-out, as exact strings.
   *
   * Two files, so the aggregate is genuinely an aggregate — a per-file figure
   * dressed up as a batch figure would pass a one-file version of this.
   */
  it('shows how much of the batch has gone, and how fast', async () => {
    const onProgress: Array<(s: { loaded: number; total: number | null; at: number }) => void> = [];
    uploadResource.mockImplementation((
      _f: File, _s: string, _fo: unknown, cb: (s: { loaded: number; total: number | null; at: number }) => void,
    ) => {
      onProgress.push(cb);
      return new Promise(() => {});
    });

    await pickFiles([bigFile('one.jpg', 3 * MIB), bigFile('two.jpg', 5 * MIB)]);
    await waitFor(() => { expect(onProgress.length).toBe(2); });

    expect(screen.getByText('Uploading 2 file(s)')).toBeInTheDocument();
    // No reading yet: "not known" must not be rendered as a speed of zero.
    expect(screen.getByText('Measuring speed…')).toBeInTheDocument();

    // Both files open at zero: the anchor the first reading is measured from.
    act(() => {
      onProgress[0]({ loaded: 0, total: 3 * MIB, at: 10_000 });
      onProgress[1]({ loaded: 0, total: 5 * MIB, at: 10_000 });
    });
    expect(screen.getByText('0 B of 8.0 MB')).toBeInTheDocument();
    expect(screen.getByText('Measuring speed…')).toBeInTheDocument();

    act(() => {
      // 1 MiB each, one second on. The first of the two closes the gap and
      // becomes the reading (1 MiB/s); the second lands in the same instant
      // and moves the bytes without pretending to be a second measurement.
      onProgress[0]({ loaded: 1 * MIB, total: 3 * MIB, at: 11_000 });
      onProgress[1]({ loaded: 1 * MIB, total: 5 * MIB, at: 11_000 });
    });
    expect(screen.getByText('2.0 MB of 8.0 MB')).toBeInTheDocument();
    expect(screen.getByText('25%')).toBeInTheDocument();
    expect(screen.getByText('≈ 1.0 MB/s')).toBeInTheDocument();

    act(() => {
      onProgress[0]({ loaded: 3 * MIB, total: 3 * MIB, at: 12_000 });
    });
    /* 4 MiB of 8 MiB. The instantaneous reading is 3 MiB/s, and the figure on
       screen is 1.6 MB/s — i.e. SMOOTHED, not adopted whole. A page printing
       the raw reading would show 3.0 here, which is the assertion that
       separates "a rate is displayed" from "the displayed rate is the one this
       page's estimator produced". */
    expect(screen.getByText('4.0 MB of 8.0 MB')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getByText('≈ 1.6 MB/s')).toBeInTheDocument();
  });

  /**
   * The browser counts multipart framing too, so `loaded` overruns `file.size`
   * at the tail. Uncapped, a finished batch reports more bytes sent than the
   * batch contains — and a percentage over 100.
   */
  it('never reports more of the batch than the batch holds', async () => {
    const onProgress: Array<(s: { loaded: number; total: number | null; at: number }) => void> = [];
    uploadResource.mockImplementation((
      _f: File, _s: string, _fo: unknown, cb: (s: { loaded: number; total: number | null; at: number }) => void,
    ) => {
      onProgress.push(cb);
      return new Promise(() => {});
    });

    /* Deliberately a TINY file, in bytes rather than megabytes. The framing is
       a couple of hundred bytes either way, so on a multi-megabyte file the
       overrun rounds away at one decimal place and an uncapped page looks
       identical — a test written at MB scale would go green with the cap
       deleted. At byte scale the difference is the whole reading. */
    await pickFiles([bigFile('one.jpg', 500)]);
    await waitFor(() => { expect(onProgress.length).toBe(1); });

    act(() => {
      onProgress[0]({ loaded: 720, total: 720, at: 20_000 });
    });
    expect(screen.getByText('500 B of 500 B')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  /**
   * ⚠️ A CANCEL THAT ONLY HIDES THE PANEL IS A PICTURE OF A CANCEL.
   *
   * So what is asserted is the SIGNAL the service was handed, not the panel
   * going away — the bytes have to actually stop.
   */
  it('really aborts the requests when the reader cancels', async () => {
    let reject: ((e: unknown) => void) | null = null;
    uploadResource.mockImplementation(() => new Promise((_res, rej) => { reject = rej; }));

    await pickFiles([bigFile('one.jpg', 1 * MIB)]);
    await waitFor(() => { expect(uploadResource).toHaveBeenCalledTimes(1); });
    const signal = uploadResource.mock.calls[0][5] as AbortSignal;
    expect(signal.aborted).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel Upload' }));
    expect(signal.aborted).toBe(true);

    // Let the batch unwind the way a real abort would, and confirm the page
    // reports the cancellation rather than reporting a failure.
    await act(async () => {
      reject?.(uploadRejection('aborted', null));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith('1 file(s) were not uploaded — you cancelled.', 'info');
    });
  });
});

/**
 * ══ TYPED FAILURE READ-OUT ══════════════════════════════════════════════════
 *
 * What this replaces said "{{n}} image(s) failed to upload" and nothing else,
 * so a user whose 500 MB shot was rejected for being 500 MB got the same
 * sentence as one whose connection dropped. The list names the file, says what
 * went wrong in our words, and quotes the backend's own where it sent any.
 */
describe('when an upload fails', () => {
  it('names the file, the reason, and the backend’s own words', async () => {
    uploadResource.mockRejectedValue(
      uploadRejection('too_large', 'File too large. Maximum size is 500 MB.'),
    );
    await pickFiles([bigFile('huge.jpg', 1 * MIB)]);

    const row = await screen.findByText('huge.jpg');
    const li = row.closest('li') as HTMLElement;
    expect(li.textContent).toBe(
      'huge.jpgThe file is over the size limit.File too large. Maximum size is 500 MB.',
    );
  });

  /** No detail from the backend means no quoted sentence — not an invented
   *  one, and not an empty pair of brackets either. */
  it('says only what it knows when the backend sent no detail', async () => {
    uploadResource.mockRejectedValue(uploadRejection('network', null));
    await pickFiles([bigFile('dropped.jpg', 1 * MIB)]);

    const li = (await screen.findByText('dropped.jpg')).closest('li') as HTMLElement;
    expect(li.textContent).toBe(
      'dropped.jpgThe connection dropped before the file finished sending.',
    );
  });

  /** An unclassified throw gets the sentence that admits as much. */
  it('admits when the reason is not one it recognises', async () => {
    uploadResource.mockRejectedValue(new Error('boom'));
    await pickFiles([bigFile('mystery.jpg', 1 * MIB)]);

    const li = (await screen.findByText('mystery.jpg')).closest('li') as HTMLElement;
    expect(li.textContent).toBe(
      'mystery.jpgThe upload failed, and the reason was not one this page recognises.',
    );
  });

  /** The successes still land. One bad file must not take the batch with it. */
  it('keeps the files that did upload', async () => {
    uploadResource
      .mockRejectedValueOnce(uploadRejection('server', null))
      .mockResolvedValueOnce({ id: 'up-2', filename: 'good.jpg' });
    await pickFiles([bigFile('bad.jpg', 1 * MIB), bigFile('good.jpg', 1 * MIB)]);

    expect(await screen.findByLabelText('good.jpg')).toBeInTheDocument();
    expect((await screen.findByText('bad.jpg')).closest('li')?.textContent)
      .toBe('bad.jpgThe server could not store the file.');
  });
});
