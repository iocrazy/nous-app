/**
 * The player's half of cross-device resume (mig 473).
 *
 * `utils/playbackResume.test.ts` pins the reconcile RULE; this pins the
 * WIRING — which calls the player makes, in what order, and the cases where it
 * deliberately makes none. The ordering matters: an unsynced local entry is
 * pushed before the read, because the rule resolves "offline write vs stale
 * write" in the server's favour and pushing first is what removes the
 * ambiguity rather than losing to it.
 */
import React, { createRef } from 'react';
import { render, act, cleanup, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { VideoPlayer } from './VideoPlayer';
import { savePosition, markSynced, getEntry } from '../utils/playbackResume';

let signedInAs: string | null = null;
vi.mock('../contexts/AuthContext', () => ({
  useOptionalAuth: () => ({ currentUserId: signedInAs }),
}));

const fetchRemotePosition = vi.fn();
const pushRemotePosition = vi.fn();
const deleteRemotePosition = vi.fn();
vi.mock('../services/playbackSyncService', () => ({
  fetchRemotePosition: (...a: unknown[]) => fetchRemotePosition(...a),
  pushRemotePosition: (...a: unknown[]) => pushRemotePosition(...a),
  deleteRemotePosition: (...a: unknown[]) => deleteRemotePosition(...a),
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

function stubDuration(video: HTMLVideoElement, seconds: number) {
  Object.defineProperty(video, 'duration', { value: seconds, configurable: true });
}

function renderPlayer(currentUserId: string | null) {
  signedInAs = currentUserId;
  const ref = createRef<HTMLVideoElement>();
  const utils = render(
    <VideoPlayer src={SRC} playerRef={ref} onTimeUpdate={() => {}} onDurationChange={() => {}} />,
  );
  return { ref, ...utils };
}

async function open(currentUserId: string | null, duration = 600) {
  const r = renderPlayer(currentUserId);
  const video = r.ref.current!;
  stubDuration(video, duration);
  await act(async () => {
    video.dispatchEvent(new Event('loadedmetadata'));
  });
  return { ...r, video };
}

beforeEach(() => {
  localStorage.clear();
  signedInAs = null;
  fetchRemotePosition.mockReset().mockResolvedValue(null);
  pushRemotePosition.mockReset().mockResolvedValue(null);
  deleteRemotePosition.mockReset().mockResolvedValue(undefined);
});
afterEach(() => cleanup());

describe('opening a video', () => {
  it('adopts a further-along position from another device', async () => {
    savePosition(ALICE, SRC, 137, 600);
    markSynced(ALICE, SRC, 'T1');
    fetchRemotePosition.mockResolvedValue({
      media_key: SRC,
      position_seconds: 410,
      duration_seconds: 600,
      updated_at: 'T2', // moved since T1 → another device wrote
    });

    const { video } = await open(ALICE);

    await waitFor(() => expect(video.currentTime).toBeCloseTo(410, 3));
    expect(getEntry(ALICE, SRC)).toMatchObject({ t: 410, sv: 'T2' });
  });

  it('keeps the local position when the server still holds this device\'s write', async () => {
    savePosition(ALICE, SRC, 200, 600);
    markSynced(ALICE, SRC, 'T1');
    fetchRemotePosition.mockResolvedValue({
      media_key: SRC,
      position_seconds: 137,
      duration_seconds: 600,
      updated_at: 'T1', // unchanged → nobody else wrote
    });

    const { video } = await open(ALICE);

    expect(video.currentTime).toBeCloseTo(200, 3);
  });

  it('pushes an unsynced local entry BEFORE reading', async () => {
    savePosition(ALICE, SRC, 200, 600); // no markSynced → never reached the server
    const calls: string[] = [];
    pushRemotePosition.mockImplementation(async () => {
      calls.push('push');
      return 'T5';
    });
    fetchRemotePosition.mockImplementation(async () => {
      calls.push('fetch');
      return { media_key: SRC, position_seconds: 200, duration_seconds: 600, updated_at: 'T5' };
    });

    await open(ALICE);

    await waitFor(() => expect(calls).toEqual(['push', 'fetch']));
    // The push made the entry provably ours, so the read does not override it.
    expect(getEntry(ALICE, SRC)).toMatchObject({ t: 200, sv: 'T5' });
  });

  it('ignores a server position that is past the end of THIS media', async () => {
    // Another device stored 590s of what it thought was a 600s clip; here the
    // media is 95s long.
    fetchRemotePosition.mockResolvedValue({
      media_key: SRC,
      position_seconds: 90,
      duration_seconds: 600,
      updated_at: 'T2',
    });

    const { video } = await open(ALICE, 95);

    await waitFor(() => expect(fetchRemotePosition).toHaveBeenCalled());
    expect(video.currentTime).toBe(0);
  });

  it('does not yank a viewer who already pressed play on this device', async () => {
    let resolveFetch: (v: unknown) => void = () => {};
    fetchRemotePosition.mockReturnValue(new Promise((r) => { resolveFetch = r; }));

    const { video } = await open(ALICE);
    Object.defineProperty(video, 'paused', { value: false, configurable: true });
    video.currentTime = 12;

    await act(async () => {
      resolveFetch({ media_key: SRC, position_seconds: 410, duration_seconds: 600, updated_at: 'T2' });
    });

    expect(video.currentTime).toBeCloseTo(12, 3);
    // Still worth remembering for the next open.
    await waitFor(() => expect(getEntry(ALICE, SRC)).toMatchObject({ t: 410 }));
  });

  it('signed-out viewing never touches the server', async () => {
    savePosition(null, SRC, 137, 600);
    const { video } = await open(null);

    expect(fetchRemotePosition).not.toHaveBeenCalled();
    expect(pushRemotePosition).not.toHaveBeenCalled();
    expect(video.currentTime).toBeCloseTo(137, 3);
  });
});

describe('writing back', () => {
  it('flushes to the server on pause, without waiting for the throttle', async () => {
    const { video } = await open(ALICE);
    await act(async () => {
      video.currentTime = 240;
      video.dispatchEvent(new Event('pause'));
    });

    await waitFor(() =>
      expect(pushRemotePosition).toHaveBeenCalledWith(SRC, 240, 600, {}),
    );
  });

  it('uses keepalive on unload, so the request outlives the page', async () => {
    const { video } = await open(ALICE);
    await act(async () => {
      video.currentTime = 240;
      window.dispatchEvent(new Event('pagehide'));
    });

    await waitFor(() =>
      expect(pushRemotePosition).toHaveBeenCalledWith(SRC, 240, 600, { keepalive: true }),
    );
  });

  it('forgets the position everywhere when the video plays out', async () => {
    savePosition(ALICE, SRC, 300, 600);
    const { video } = await open(ALICE);
    await act(async () => {
      video.dispatchEvent(new Event('ended'));
    });

    await waitFor(() => expect(deleteRemotePosition).toHaveBeenCalledWith(SRC));
    expect(getEntry(ALICE, SRC)).toBeNull();
  });

  it('a failed push leaves the entry unsynced, so the next open retries it', async () => {
    pushRemotePosition.mockResolvedValue(null); // offline
    const { video } = await open(ALICE);
    await act(async () => {
      video.currentTime = 240;
      video.dispatchEvent(new Event('pause'));
    });

    await waitFor(() => expect(pushRemotePosition).toHaveBeenCalled());
    expect(getEntry(ALICE, SRC)?.sv).toBeUndefined();
  });
});
