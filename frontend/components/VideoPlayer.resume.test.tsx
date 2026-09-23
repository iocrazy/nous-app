/**
 * The player remembers where playback got to.
 *
 * The bug this pins: a deploy re-mounts the tab mid-video (the service worker
 * updates in the background, and a stale chunk can force a reload), and
 * playback restarted at 00:00. A 20-minute review lost its place because a
 * build shipped.
 *
 * jsdom has no media pipeline, so these drive the element's events directly —
 * that IS the seam the component listens on, so it is the right seam to test.
 */
import React, { createRef } from 'react';
import { render, act, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { VideoPlayer } from './VideoPlayer';
import { savePosition, loadPosition } from '../utils/playbackResume';

/** Whoever is signed in for the current test. Mocked rather than provided,
 *  so this file does not force `AuthContext` to become public API. */
let signedInAs: string | null = null;
vi.mock('../contexts/AuthContext', () => ({
  useOptionalAuth: () => ({ currentUserId: signedInAs }),
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

const SRC = '/media/12345';
const ALICE = 'user-alice';
const BOB = 'user-bob';

/** jsdom leaves `duration` as NaN; the component reads it on every save. */
function stubDuration(video: HTMLVideoElement, seconds: number) {
  Object.defineProperty(video, 'duration', { value: seconds, configurable: true });
}

function renderPlayer(currentUserId: string | null = null) {
  signedInAs = currentUserId;
  const ref = createRef<HTMLVideoElement>();
  const utils = render(
    <VideoPlayer
      src={SRC}
      playerRef={ref}
      onTimeUpdate={() => {}}
      onDurationChange={() => {}}
    />,
  );
  return { ref, ...utils };
}

function loadMetadata(video: HTMLVideoElement, seconds: number) {
  stubDuration(video, seconds);
  act(() => {
    video.dispatchEvent(new Event('loadedmetadata'));
  });
}

beforeEach(() => {
  localStorage.clear();
  signedInAs = null;
});
afterEach(() => {
  cleanup();
});

describe('resume', () => {
  it('seeks to the stored position on loadedmetadata', () => {
    savePosition(null, SRC, 137, 600);

    const { ref } = renderPlayer();
    const video = ref.current!;
    stubDuration(video, 600);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });

    expect(video.currentTime).toBeCloseTo(137, 3);
  });

  it('starts at zero when nothing is stored', () => {
    const { ref } = renderPlayer();
    const video = ref.current!;
    stubDuration(video, 600);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });

    expect(video.currentTime).toBe(0);
  });

  it('records progress while playing', () => {
    const { ref } = renderPlayer();
    const video = ref.current!;
    stubDuration(video, 600);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
      video.currentTime = 240;
      video.dispatchEvent(new Event('timeupdate'));
    });

    expect(loadPosition(null, SRC, 600)).toBeCloseTo(240, 3);
  });

  it('flushes on unmount — the reload case, where no pause event ever fires', () => {
    const { ref, unmount } = renderPlayer();
    const video = ref.current!;
    stubDuration(video, 600);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
      video.currentTime = 90;
      // deliberately NO timeupdate: the throttle window has not elapsed
    });
    unmount();

    expect(loadPosition(null, SRC, 600)).toBeCloseTo(90, 3);
  });

  it('forgets the position once the video ends', () => {
    savePosition(null, SRC, 300, 600);
    const { ref } = renderPlayer();
    const video = ref.current!;
    stubDuration(video, 600);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
      video.dispatchEvent(new Event('ended'));
    });

    expect(loadPosition(null, SRC, 600)).toBeNull();
  });

  it('does not re-seek on a second loadedmetadata (quality switch keeps its own position)', () => {
    savePosition(null, SRC, 137, 600);
    const { ref } = renderPlayer();
    const video = ref.current!;
    stubDuration(video, 600);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(video.currentTime).toBeCloseTo(137, 3);

    // A quality switch restores its own position, then metadata fires again.
    act(() => {
      video.currentTime = 400;
      video.dispatchEvent(new Event('loadedmetadata'));
    });

    expect(video.currentTime).toBeCloseTo(400, 3);
  });
});

describe('volume memory', () => {
  it('applies the stored volume to a freshly attached element', () => {
    localStorage.setItem('nous_volume_pref', JSON.stringify({ volume: 0.3, muted: false }));

    const { ref } = renderPlayer();
    const video = ref.current!;
    stubDuration(video, 600);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });

    expect(video.volume).toBeCloseTo(0.3, 3);
    expect(video.muted).toBe(false);
  });
});


describe('per-user isolation', () => {
  it('does not resume one account at another account\'s position', () => {
    // Alice watched to 2:17 of a shared library video on this machine.
    savePosition(ALICE, SRC, 137, 600);

    const { ref } = renderPlayer(BOB);
    loadMetadata(ref.current!, 600);

    expect(ref.current!.currentTime).toBe(0);
  });

  it('resumes the account that actually watched', () => {
    savePosition(ALICE, SRC, 137, 600);

    const { ref } = renderPlayer(ALICE);
    loadMetadata(ref.current!, 600);

    expect(ref.current!.currentTime).toBeCloseTo(137, 3);
  });

  it('writes under the signed-in account, not the shared namespace', () => {
    const { ref } = renderPlayer(ALICE);
    const video = ref.current!;
    loadMetadata(video, 600);

    act(() => {
      video.currentTime = 240;
      video.dispatchEvent(new Event('timeupdate'));
    });

    expect(loadPosition(ALICE, SRC, 600)).toBeCloseTo(240, 3);
    expect(loadPosition(BOB, SRC, 600)).toBeNull();
    expect(loadPosition(null, SRC, 600)).toBeNull();
  });
});
