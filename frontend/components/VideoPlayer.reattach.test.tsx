/**
 * Re-initialising the SAME media must not lose the viewer's place.
 *
 * Reported from production: a video paused at 05:13, the user switched browser
 * tabs, came back, and it was at 00:00 on frame 0. It was not a page reload —
 * localStorage survives those and would have restored the position. It was the
 * element being re-initialised in place, which happens whenever a prop the
 * source-attach effect depends on changes (the auth token is the one that
 * churns), and also when the browser drops a backgrounded tab's media.
 *
 * The player's own guard was the reason nothing came back: it allowed a resume
 * once per media key, so the second `loadedmetadata` for the same video was
 * ignored. That guard exists for quality switches, which restore their own
 * position and must not be seeked twice — these tests pin both behaviours so
 * fixing one cannot quietly break the other.
 */
import React, { createRef } from 'react';
import { render, act, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { VideoPlayer } from './VideoPlayer';
import { savePosition } from '../utils/playbackResume';

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

const SRC = '/media/12345';

function stubDuration(video: HTMLVideoElement, seconds: number) {
  Object.defineProperty(video, 'duration', { value: seconds, configurable: true });
}

function renderPlayer(authToken?: string) {
  const ref = createRef<HTMLVideoElement>();
  const utils = render(
    <VideoPlayer
      src={SRC}
      authToken={authToken}
      playerRef={ref}
      onTimeUpdate={() => {}}
      onDurationChange={() => {}}
    />,
  );
  const rerenderWithToken = (token: string) =>
    utils.rerender(
      <VideoPlayer
        src={SRC}
        authToken={token}
        playerRef={ref}
        onTimeUpdate={() => {}}
        onDurationChange={() => {}}
      />,
    );
  return { ref, rerenderWithToken, ...utils };
}

/** What the browser does to the element when the source is re-attached. */
function reinitialise(video: HTMLVideoElement) {
  video.currentTime = 0;
  video.dispatchEvent(new Event('loadedmetadata'));
}

beforeEach(() => localStorage.clear());
afterEach(() => cleanup());

describe('in-place re-initialisation', () => {
  it('restores the position when the same media is re-attached', () => {
    savePosition(null, SRC, 313, 598); // 05:13 of 09:58, the reported case

    const { ref, rerenderWithToken } = renderPlayer('token-1');
    const video = ref.current!;
    stubDuration(video, 598);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(video.currentTime).toBeCloseTo(313, 3);

    // The auth token is refreshed while the tab is in the background; coming
    // back re-runs the source-attach effect and the element starts over.
    // Two separate acts on purpose — that is the real order: the effect runs
    // first (it is what re-points the element), the reset follows.
    act(() => {
      rerenderWithToken('token-2');
    });
    act(() => {
      reinitialise(video);
    });

    expect(video.currentTime).toBeCloseTo(313, 3);
  });

  it('carries the LIVE position, not the last one written to storage', () => {
    // Storage lags by up to a second (the save is throttled), and the viewer
    // may have moved since. The element's own position is the truth.
    savePosition(null, SRC, 100, 598);

    const { ref, rerenderWithToken } = renderPlayer('token-1');
    const video = ref.current!;
    stubDuration(video, 598);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
      video.currentTime = 420;
      video.dispatchEvent(new Event('timeupdate'));
    });

    act(() => {
      rerenderWithToken('token-2');
    });
    act(() => {
      reinitialise(video);
    });

    expect(video.currentTime).toBeCloseTo(420, 3);
  });

  it('does not re-seek on a second loadedmetadata within one attachment', () => {
    // A quality switch restores its own position and fires metadata again;
    // seeking a second time would undo it.
    savePosition(null, SRC, 313, 598);

    const { ref } = renderPlayer('token-1');
    const video = ref.current!;
    stubDuration(video, 598);

    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(video.currentTime).toBeCloseTo(313, 3);

    act(() => {
      video.currentTime = 500; // the switch put us here
      video.dispatchEvent(new Event('loadedmetadata'));
    });

    expect(video.currentTime).toBeCloseTo(500, 3);
  });

  it('a genuinely different video starts from ITS own position, not the old one', () => {
    savePosition(null, SRC, 313, 598);

    const ref = createRef<HTMLVideoElement>();
    const { rerender } = render(
      <VideoPlayer src={SRC} playerRef={ref} onTimeUpdate={() => {}} onDurationChange={() => {}} />,
    );
    const video = ref.current!;
    stubDuration(video, 598);
    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(video.currentTime).toBeCloseTo(313, 3);

    act(() => {
      rerender(
        <VideoPlayer
          src="/media/99999"
          playerRef={ref}
          onTimeUpdate={() => {}}
          onDurationChange={() => {}}
        />,
      );
    });
    act(() => {
      reinitialise(video);
    });

    expect(video.currentTime).toBe(0);
  });
});
