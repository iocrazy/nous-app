/**
 * The frame grabber. What the component is responsible for:
 *
 *   1. Grabbing sends a TIMESTAMP to the server — never the <video> pixels.
 *      The server re-reads the frame from the source at full quality; a
 *      screenshot of a possibly-downscaled stream would be a worse image that
 *      happens to look right.
 *   2. The video is PAUSED before the timestamp is read. On a playing video the
 *      clock advances between the click and the read — the user grabs the frame
 *      they saw, not one slightly later.
 *   3. With no video there is a sentence, not a dead button. With a full pool
 *      the button says WHY it is disabled.
 *   4. Already-grabbed timestamps render as dots positioned by time/duration.
 *   5. A failed grab shows the server's own sentence when there is one.
 *
 * jsdom note: HTMLMediaElement has no real playback here, so duration arrives
 * via a synthesized loadedmetadata event and currentTime is set directly.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';
import type { LibraryVideo } from '../../../types';

const { grabCoverFrame } = vi.hoisted(() => ({ grabCoverFrame: vi.fn() }));
vi.mock('../../../services/distributionService', () => ({ grabCoverFrame }));

vi.mock('../../../services/resourceService', () => ({
  getResourceFileUrl: (id: string, token?: string) =>
    `https://api.test/api/v1/resources/${id}/file${token ? `?token=${token}` : ''}`,
}));

vi.mock('../../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: () =>
        Promise.resolve({ data: { session: { access_token: 'jwt' } } }),
    },
  }),
}));

import { CoverFrameGrabber } from './CoverFrameGrabber';

const VIDEO: LibraryVideo = {
  id: '900',
  filename: 'clip-a.mp4',
  thumbnail_url: null,
};

const GRAB_RESULT = {
  generated_media_id: '341582104263581',
  url: '/api/v1/generated-media/341582104263581/cover',
  timestamp_seconds: 3.1,
};

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

function renderGrabber(
  over: Partial<React.ComponentProps<typeof CoverFrameGrabber>> = {},
) {
  const onGrabbed = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverFrameGrabber
        sources={[VIDEO]}
        grabbedAt={[]}
        onGrabbed={onGrabbed}
        {...over}
      />
    </I18nextProvider>,
  );
  return { onGrabbed };
}

/** Give the jsdom <video> a duration and a current position. */
function primeVideo(seconds: number, duration = 120) {
  const video = screen.getByTestId('cover-grab-video') as HTMLVideoElement;
  Object.defineProperty(video, 'duration', { value: duration, configurable: true });
  Object.defineProperty(video, 'currentTime', {
    value: seconds,
    writable: true,
    configurable: true,
  });
  Object.defineProperty(video, 'pause', { value: vi.fn(), configurable: true });
  fireEvent.loadedMetadata(video);
  fireEvent.timeUpdate(video);
  return video;
}

beforeEach(() => {
  vi.clearAllMocks();
  grabCoverFrame.mockResolvedValue(GRAB_RESULT);
});

describe('CoverFrameGrabber', () => {
  it('grabs by TIMESTAMP, pausing first', async () => {
    const { onGrabbed } = renderGrabber();
    const video = primeVideo(66.42);

    fireEvent.click(screen.getByTestId('cover-grab-button'));

    await waitFor(() =>
      expect(grabCoverFrame).toHaveBeenCalledWith('900', 66.42),
    );
    // Paused BEFORE the timestamp was read — on a playing video the clock
    // advances between the click and the read.
    expect(video.pause).toHaveBeenCalled();
    await waitFor(() =>
      expect(onGrabbed).toHaveBeenCalledWith({
        generatedMediaId: '341582104263581',
        url: '/api/v1/generated-media/341582104263581/cover',
        timestampSeconds: 3.1,
      }),
    );
  });

  it('says what is missing instead of rendering a dead button', () => {
    renderGrabber({ sources: [] });

    expect(screen.getByText(/Pick a video on the publish page first/)).toBeTruthy();
    expect(screen.queryByTestId('cover-grab-button')).toBeNull();
  });

  it('says WHY the button is disabled when the pool is full', () => {
    renderGrabber({ poolFull: true });
    primeVideo(3);

    const btn = screen.getByTestId('cover-grab-button') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('Pool is full');
    fireEvent.click(btn);
    expect(grabCoverFrame).not.toHaveBeenCalled();
  });

  it('draws one dot per already-grabbed timestamp, positioned by time', () => {
    renderGrabber({ grabbedAt: [12, 60] });
    primeVideo(0, 120);

    const marks = screen.getAllByTestId('cover-grab-mark');
    expect(marks).toHaveLength(2);
    expect(marks[0].style.left).toBe('10%');
    expect(marks[1].style.left).toBe('50%');
  });

  it('draws no dots before the duration is known', () => {
    // Without a duration the division is by zero and every dot lands at
    // Infinity% — off-screen but "present", the DOM version of a lie.
    renderGrabber({ grabbedAt: [12] });

    expect(screen.getByTestId('cover-grab-track')).toBeTruthy();
    expect(screen.queryAllByTestId('cover-grab-mark')).toHaveLength(0);
  });

  it('shows the server sentence when the grab fails with one', async () => {
    grabCoverFrame.mockRejectedValueOnce(
      Object.assign(new Error('http'), {
        detail: 'could not re-read the frame at 3.1s — sample the video again',
      }),
    );
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { onGrabbed } = renderGrabber();
    primeVideo(3.1);

    fireEvent.click(screen.getByTestId('cover-grab-button'));

    expect(await screen.findByTestId('cover-grab-error')).toBeTruthy();
    expect(screen.getByTestId('cover-grab-error').textContent).toContain(
      'could not re-read',
    );
    expect(onGrabbed).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it('falls back to its own sentence when the failure carries none', async () => {
    grabCoverFrame.mockRejectedValueOnce(new Error('boom'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderGrabber();
    primeVideo(3.1);

    fireEvent.click(screen.getByTestId('cover-grab-button'));

    expect(await screen.findByTestId('cover-grab-error')).toBeTruthy();
    expect(screen.getByTestId('cover-grab-error').textContent).toMatch(
      /could not be grabbed/,
    );
    spy.mockRestore();
  });

  it('offers a source picker only when there is a choice to make', () => {
    renderGrabber({
      sources: [VIDEO, { id: '901', filename: 'clip-b.mp4', thumbnail_url: null }],
    });

    expect(screen.getByLabelText('Grab from')).toBeTruthy();
  });

  it('hides the source picker for a single video', () => {
    renderGrabber();

    expect(screen.getByTestId('cover-grab-video')).toBeTruthy();
    expect(screen.queryByLabelText('Grab from')).toBeNull();
  });
});

describe('CoverFrameGrabber — stage mode (v4)', () => {
  it('draws a crop guide in the cover’s aspect and offers the no-AI path', async () => {
    const onUseAsCover = vi.fn().mockResolvedValue(undefined);
    renderGrabber({ embedded: true, aspect: '4:3', onUseAsCover });
    primeVideo(3.1);

    expect(screen.getByTestId('cover-crop-guide').className).toContain('h');
    fireEvent.click(screen.getByTestId('cover-use-frame'));

    await waitFor(() => expect(onUseAsCover).toHaveBeenCalledWith('900', 3.1, { x: 0.5, y: 0.5 }));
    // The AI grab is still there too — the two paths are not either/or.
    expect(screen.getByTestId('cover-grab-button')).toBeTruthy();
  });

  it('says so when the frame cannot become a cover, and stays usable', async () => {
    const onUseAsCover = vi.fn().mockRejectedValue(new Error('504'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderGrabber({ embedded: true, onUseAsCover });
    primeVideo(3.1);

    fireEvent.click(screen.getByTestId('cover-use-frame'));

    expect(await screen.findByTestId('cover-grab-error')).toBeTruthy();
    expect((screen.getByTestId('cover-use-frame') as HTMLButtonElement).disabled).toBe(false);
    spy.mockRestore();
  });

  it('refuses a non-image upload before calling anyone', () => {
    const onUploadCover = vi.fn().mockResolvedValue(undefined);
    renderGrabber({ embedded: true, onUploadCover });

    const input = screen.getByTestId('cover-upload-cover-input') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'clip.mp4', { type: 'video/mp4' })] },
    });

    expect(onUploadCover).not.toHaveBeenCalled();
    expect(screen.getByTestId('cover-grab-error')).toBeTruthy();
  });

  it('hands an image upload straight through', async () => {
    const onUploadCover = vi.fn().mockResolvedValue(undefined);
    renderGrabber({ embedded: true, onUploadCover });
    const file = new File(['x'], 'cover.png', { type: 'image/png' });

    fireEvent.change(screen.getByTestId('cover-upload-cover-input'), {
      target: { files: [file] },
    });

    await waitFor(() => expect(onUploadCover).toHaveBeenCalledWith(file));
  });

  it('shows neither extra button in card mode', () => {
    renderGrabber();
    expect(screen.queryByTestId('cover-use-frame')).toBeNull();
    expect(screen.queryByTestId('cover-upload-cover')).toBeNull();
    expect(screen.queryByTestId('cover-crop-guide')).toBeNull();
  });
});


describe('CoverFrameGrabber — the crop box moves', () => {
  it('starts centred and the arrow keys nudge the anchor, clamped to the picture', async () => {
    const onUseAsCover = vi.fn().mockResolvedValue(undefined);
    renderGrabber({ embedded: true, onUseAsCover });
    primeVideo(3.1);
    const box = screen.getByTestId('cover-crop-guide');
    expect(box.getAttribute('data-focus-x')).toBe('0.50');

    fireEvent.keyDown(box, { key: 'ArrowRight' });
    fireEvent.keyDown(box, { key: 'ArrowRight' });
    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(box.getAttribute('data-focus-x')).toBe('0.60');
    expect(box.getAttribute('data-focus-y')).toBe('0.45');

    for (let i = 0; i < 20; i += 1) fireEvent.keyDown(box, { key: 'ArrowLeft' });
    expect(box.getAttribute('data-focus-x')).toBe('0.00');

    fireEvent.click(screen.getByTestId('cover-use-frame'));
    await waitFor(() => expect(onUseAsCover).toHaveBeenCalledWith('900', 3.1, { x: 0, y: 0.45 }));
  });

  it('resets the anchor when the source video changes', () => {
    const { rerender } = render(
      <I18nextProvider i18n={makeI18n()}>
        <CoverFrameGrabber sources={[VIDEO]} grabbedAt={[]} onGrabbed={vi.fn()} embedded />
      </I18nextProvider>,
    );
    const box = screen.getByTestId('cover-crop-guide');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(box.getAttribute('data-focus-y')).toBe('0.55');

    rerender(
      <I18nextProvider i18n={makeI18n()}>
        <CoverFrameGrabber sources={[{ ...VIDEO, id: '901' }]} grabbedAt={[]} onGrabbed={vi.fn()} embedded />
      </I18nextProvider>,
    );
    expect(screen.getByTestId('cover-crop-guide').getAttribute('data-focus-y')).toBe('0.50');
  });
});
