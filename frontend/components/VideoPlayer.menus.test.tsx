/**
 * The control bar's three popups, as rendered.
 *
 * Reported: "speed, volume and quality should all open above the bar on hover,
 * and look the same — right now it's ugly." Two separate defects behind that:
 *
 *   1. Only volume opened on hover. Speed and quality needed a click.
 *   2. The popups rendered as WHITE cards on the black bar. They used `ink-*`
 *      tokens, and in the light theme that scale is inverted (`ink-900` is
 *      #F9F7F3). The player sits over video, so it must use the dark palette
 *      whatever the app theme is.
 *
 * `usePlayerMenus` is tested on its own; this pins the wiring in the component.
 */
import React, { createRef } from 'react';
import { render, act, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { VideoPlayer } from './VideoPlayer';

vi.mock('../contexts/AuthContext', () => ({
  useOptionalAuth: () => ({ currentUserId: null }),
}));
vi.mock('../services/playbackSyncService', () => ({
  fetchRemotePosition: vi.fn().mockResolvedValue(null),
  pushRemotePosition: vi.fn().mockResolvedValue(null),
  deleteRemotePosition: vi.fn().mockResolvedValue(undefined),
}));
// A multi-level HLS stream, so the quality menu renders at all.
vi.mock('hls.js', () => {
  class FakeHls {
    static isSupported = () => true;
    static Events = {
      MANIFEST_PARSED: 'm',
      LEVEL_SWITCHED: 'l',
      ERROR: 'e',
      FRAG_BUFFERED: 'f',
    };
    static ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' };
    handlers: Record<string, (e: unknown, d: unknown) => void> = {};
    levels = [
      { height: 1080, width: 1920, bitrate: 5_000_000, name: '1080p' },
      { height: 720, width: 1280, bitrate: 2_500_000, name: '720p' },
    ];
    currentLevel = -1;
    on(ev: string, fn: (e: unknown, d: unknown) => void) {
      this.handlers[ev] = fn;
      if (ev === 'm') queueMicrotask(() => fn(ev, { levels: this.levels }));
    }
    loadSource() {}
    attachMedia() {}
    destroy() {}
  }
  return { default: FakeHls };
});

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const renderPlayer = async () => {
  const ref = createRef<HTMLVideoElement>();
  const utils = render(
    <VideoPlayer
      src="https://api.example/hls/master.m3u8?token=t"
      originalSrc="https://api.example/file.mp4?token=t"
      playerRef={ref}
      onTimeUpdate={() => {}}
      onDurationChange={() => {}}
    />,
  );
  // Let MANIFEST_PARSED land so the quality menu appears.
  await act(async () => {
    await Promise.resolve();
  });
  return utils;
};

const wrapper = (container: HTMLElement, id: string) =>
  container.querySelector(`[data-player-menu="${id}"]`) as HTMLElement;

describe('VideoPlayer control-bar popups', () => {
  it('pins the player to the dark palette', async () => {
    // Without this the popups resolve `ink-900` against the light theme and
    // render as white cards — the "ugly" in the report.
    const { container } = await renderPlayer();
    const root = container.firstElementChild as HTMLElement;
    expect(root.getAttribute('data-theme')).toBe('dark');
  });

  it.each(['speed', 'volume', 'quality'])(
    'opens the %s popup on hover, not only on click',
    async (id) => {
      const { container } = await renderPlayer();
      const el = wrapper(container, id);
      expect(el, `${id} wrapper missing`).toBeTruthy();

      act(() => {
        fireEvent.mouseEnter(el);
      });

      // Every popup carries the shared surface; finding it inside the wrapper
      // is what "opened" means here.
      expect(el.querySelector('.backdrop-blur-md')).toBeTruthy();
    },
  );

  it('uses one surface for all three popups', async () => {
    const { container } = await renderPlayer();
    const classes: string[] = [];
    for (const id of ['speed', 'volume', 'quality']) {
      const el = wrapper(container, id);
      act(() => {
        fireEvent.mouseEnter(el);
      });
      const surface = el.querySelector('.backdrop-blur-md') as HTMLElement;
      // The popup's own classes minus layout (width / padding / flex), which
      // legitimately differ between a list and a slider.
      classes.push(
        surface.className
          .split(/\s+/)
          .filter((c) => /^(bg-|ring|rounded|shadow|backdrop)/.test(c))
          .sort()
          .join(' '),
      );
      act(() => {
        fireEvent.mouseLeave(el);
        vi.advanceTimersByTime(500);
      });
    }
    expect(new Set(classes).size).toBe(1);
  });

  it('shows only one popup at a time when sweeping across the bar', async () => {
    const { container } = await renderPlayer();
    act(() => {
      fireEvent.mouseEnter(wrapper(container, 'speed'));
    });
    act(() => {
      fireEvent.mouseLeave(wrapper(container, 'speed'));
      fireEvent.mouseEnter(wrapper(container, 'quality'));
    });

    expect(container.querySelectorAll('.backdrop-blur-md')).toHaveLength(1);
    expect(wrapper(container, 'quality').querySelector('.backdrop-blur-md')).toBeTruthy();
  });

  it('keeps the hover area continuous from button to popup', async () => {
    // A margin between them is a gap the pointer falls through on its way up,
    // closing the menu before it can be reached. The bridge is padding on the
    // anchor, and every popup must have it.
    const { container } = await renderPlayer();
    for (const id of ['speed', 'volume', 'quality']) {
      const el = wrapper(container, id);
      act(() => {
        fireEvent.mouseEnter(el);
      });
      const anchor = el.querySelector('.bottom-full') as HTMLElement;
      expect(anchor.className, id).toContain('pb-2');
      expect(anchor.className, id).not.toMatch(/\bmb-\d/);
      act(() => {
        fireEvent.mouseLeave(el);
        vi.advanceTimersByTime(500);
      });
    }
  });

  it('still opens on click, for touch and keyboard', async () => {
    const { container, getByTitle } = await renderPlayer();
    act(() => {
      fireEvent.click(getByTitle(/Playback Speed/));
    });
    expect(wrapper(container, 'speed').querySelector('[role="menu"]')).toBeTruthy();
  });
});
