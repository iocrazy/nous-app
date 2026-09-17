/**
 * A rotated media token must not restart the video.
 *
 * Reported from production, with a screenshot that named the cause precisely:
 * black frame, spinner, and `00:00.00 [F0] / 00:00` — the duration back to
 * zero. Only a re-run of the source-attach effect resets React's `duration`;
 * the element re-initialising on its own would have left it alone. So the
 * effect was re-running.
 *
 * Why it re-ran: `DownloadDetailPage` builds the media URL inline during
 * render, with the current media token in the query string. Supabase refreshes
 * that token when a long-open tab returns to the foreground, so `src` became a
 * new string for the same video. (`ResourceDetailPage` keeps its URL in state,
 * which is why the same gesture there looked fine — and why this went unseen
 * for a while.)
 *
 * Swapping `src` does not even deliver the new token to a playing element: it
 * keeps streaming from the URL it opened with. So the correct behaviour is to
 * leave the element alone and let later requests read the fresh token.
 */
import React, { createRef } from 'react';
import { render, act, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { VideoPlayer, mediaIdentity } from './VideoPlayer';

vi.mock('../contexts/AuthContext', () => ({
  useOptionalAuth: () => ({ currentUserId: null }),
}));
vi.mock('../services/playbackSyncService', () => ({
  fetchRemotePosition: vi.fn().mockResolvedValue(null),
  pushRemotePosition: vi.fn().mockResolvedValue(null),
  deleteRemotePosition: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('hls.js', () => {
  class FakeHls {
    static isSupported = () => false;
    static Events = { MANIFEST_PARSED: 'm', LEVEL_SWITCHED: 'l', ERROR: 'e', FRAG_BUFFERED: 'f' };
    static ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' };
    on() {}
    loadSource() {}
    attachMedia() {}
    destroy() {}
  }
  return { default: FakeHls };
});

const BASE = 'https://api.example/api/v1/media/777';
const withToken = (t: string) => `${BASE}?token=${t}`;

beforeEach(() => localStorage.clear());
afterEach(() => cleanup());

describe('mediaIdentity', () => {
  it('ignores the rotating credential', () => {
    expect(mediaIdentity(withToken('AAA'))).toBe(mediaIdentity(withToken('BBB')));
  });

  it('keeps every other query parameter — they can select content', () => {
    expect(mediaIdentity(`${BASE}?full=1&token=A`)).not.toBe(mediaIdentity(`${BASE}?token=A`));
  });

  it('separates genuinely different media', () => {
    expect(mediaIdentity(withToken('A'))).not.toBe(
      mediaIdentity('https://api.example/api/v1/media/888?token=A'),
    );
  });

  it('also strips access_token', () => {
    expect(mediaIdentity(`${BASE}?access_token=A`)).toBe(mediaIdentity(BASE));
  });

  it('passes through a value it cannot parse', () => {
    expect(mediaIdentity('blob:https://app/abc')).toBe('blob:https://app/abc');
    expect(mediaIdentity('')).toBe('');
  });
});

describe('the player across a token rotation', () => {
  function renderWith(src: string, originalSrc?: string) {
    const ref = createRef<HTMLVideoElement>();
    const el = (s: string, o?: string) => (
      <VideoPlayer
        src={s}
        originalSrc={o}
        authToken="tok-1"
        playerRef={ref}
        resumeKey="resource:777"
        onTimeUpdate={() => {}}
        onDurationChange={() => {}}
      />
    );
    const utils = render(el(src, originalSrc));
    // The spinner is what the viewer sees as the flash, and only the
    // source-attach effect puts it back (`setIsLoading(true)`). Asserting on it
    // catches a re-attach that leaves `video.src` looking identical — which is
    // exactly the `originalSrc` case below.
    const spinning = () => !!utils.container.querySelector('.animate-spin');
    // `...utils` FIRST: spreading it last silently overwrites this `rerender`
    // with testing-library's own, and the tests then pass vacuously.
    return { ...utils, ref, spinning, rerender: (s: string, o?: string) => utils.rerender(el(s, o)) };
  }

  it('does not re-point the element when only the token changed', () => {
    const { ref, rerender, spinning } = renderWith(withToken('AAA'));
    const video = ref.current!;
    Object.defineProperty(video, 'duration', { value: 598, configurable: true });

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
      video.currentTime = 313;
      video.dispatchEvent(new Event('timeupdate'));
    });
    const srcBefore = video.getAttribute('src');

    act(() => {
      rerender(withToken('BBB'));
    });

    expect(video.getAttribute('src')).toBe(srcBefore);
    expect(video.currentTime).toBeCloseTo(313, 3);
    expect(spinning()).toBe(false);
  });

  it('does not re-point when only `originalSrc`\'s token changed', () => {
    // The HLS case: `src` is the playlist, `originalSrc` the direct file. Both
    // carry the token, and `originalSrc` is an attach dependency too.
    const { ref, rerender, spinning } = renderWith(withToken('AAA'), `${BASE}/file?token=AAA`);
    const video = ref.current!;
    Object.defineProperty(video, 'duration', { value: 598, configurable: true });

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
      video.currentTime = 313;
      video.dispatchEvent(new Event('timeupdate'));
    });
    const srcBefore = video.getAttribute('src');

    act(() => {
      rerender(withToken('AAA'), `${BASE}/file?token=BBB`);
    });

    expect(video.getAttribute('src')).toBe(srcBefore);
    expect(video.currentTime).toBeCloseTo(313, 3);
    // `src` alone cannot tell here: a re-attach would re-point it to the SAME
    // string. The spinner is the witness.
    expect(spinning()).toBe(false);
  });

  it('DOES re-point when the video actually changes', () => {
    const { ref, rerender, spinning } = renderWith(withToken('AAA'));
    const video = ref.current!;
    Object.defineProperty(video, 'duration', { value: 598, configurable: true });

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });

    act(() => {
      rerender('https://api.example/api/v1/media/888?token=AAA');
    });

    expect(video.getAttribute('src')).toContain('/media/888');
    // The negative controls above are only meaningful if the spinner really
    // does come back when the media genuinely changes.
    expect(spinning()).toBe(true);
  });
});
