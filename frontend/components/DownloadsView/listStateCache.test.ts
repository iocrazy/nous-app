import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  applyScrollOffsets,
  clearDownloadsListState,
  LIST_STATE_TTL_MS,
  readDownloadsListState,
  readScrollOffsets,
  saveDownloadsListState,
  type DownloadsListState,
} from './listStateCache';

function makeState(overrides: Partial<DownloadsListState> = {}): DownloadsListState {
  return {
    teamId: 'team-1',
    scrollTop: 1200,
    windowScrollY: 0,
    searchQuery: 'memory',
    isSearchActive: true,
    searchQueryText: 'memory',
    searchResults: [{ platform_id: 'pid-1', title: 'Test Clip' }] as any,
    searchVideoMap: { 'pid-1': { platform_id: 'pid-1' } as any },
    mobileSearchQuery: '',
    isMobileSearchOpen: false,
    savedAt: Date.now(),
    ...overrides,
  };
}

/** jsdom reports 0 for every layout box, so fake a scrollable container. */
function makeScroller(scrollHeight: number, clientHeight: number): HTMLElement {
  const el = document.createElement('div');
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true });
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true });
  return el;
}

describe('downloads list state cache', () => {
  beforeEach(() => {
    clearDownloadsListState();
  });

  it('returns the snapshot that was written for the same team', () => {
    const state = makeState();
    saveDownloadsListState(state);

    const restored = readDownloadsListState('team-1');
    expect(restored).not.toBeNull();
    expect(restored?.scrollTop).toBe(1200);
    expect(restored?.searchQuery).toBe('memory');
    expect(restored?.isSearchActive).toBe(true);
    expect(restored?.searchResults).toHaveLength(1);
    expect(restored?.searchVideoMap['pid-1']).toBeTruthy();
  });

  it('misses on a cold cache', () => {
    expect(readDownloadsListState('team-1')).toBeNull();
  });

  it('drops the snapshot when the workspace changed', () => {
    saveDownloadsListState(makeState({ teamId: 'team-1' }));

    expect(readDownloadsListState('team-2')).toBeNull();
    // …and does not resurrect it for the original team afterwards.
    expect(readDownloadsListState('team-1')).toBeNull();
  });

  it('treats the personal workspace (null team) as its own scope', () => {
    saveDownloadsListState(makeState({ teamId: null }));
    expect(readDownloadsListState(null)).not.toBeNull();
  });

  it('expires a snapshot older than the TTL', () => {
    const savedAt = Date.now();
    saveDownloadsListState(makeState({ savedAt }));

    expect(readDownloadsListState('team-1', savedAt + LIST_STATE_TTL_MS - 1)).not.toBeNull();
    expect(readDownloadsListState('team-1', savedAt + LIST_STATE_TTL_MS + 1)).toBeNull();
  });

  it('forgets everything after an explicit clear (Refresh / pull-to-refresh)', () => {
    saveDownloadsListState(makeState());
    clearDownloadsListState();
    expect(readDownloadsListState('team-1')).toBeNull();
  });
});

describe('readScrollOffsets', () => {
  it('captures both the container and the document offset', () => {
    const el = makeScroller(5000, 800);
    el.scrollTop = 640;
    vi.spyOn(window, 'scrollY', 'get').mockReturnValue(120);

    expect(readScrollOffsets(el)).toEqual({ scrollTop: 640, windowScrollY: 120 });
  });

  it('is safe when the scroller is gone', () => {
    vi.spyOn(window, 'scrollY', 'get').mockReturnValue(0);
    expect(readScrollOffsets(null)).toEqual({ scrollTop: 0, windowScrollY: 0 });
  });
});

describe('applyScrollOffsets', () => {
  it('restores the offset once the list is tall enough', () => {
    const el = makeScroller(5000, 800);

    const settled = applyScrollOffsets(el, { scrollTop: 1200, windowScrollY: 0 });

    expect(settled).toBe(true);
    expect(el.scrollTop).toBe(1200);
  });

  it('refuses to land halfway while the list is still short', () => {
    const el = makeScroller(900, 800); // max scroll = 100

    const settled = applyScrollOffsets(el, { scrollTop: 1200, windowScrollY: 0 });

    expect(settled).toBe(false);
    expect(el.scrollTop).toBe(0);
  });

  it('restores the document offset on the mobile layout', () => {
    Object.defineProperty(document.documentElement, 'scrollHeight', {
      value: 4000,
      configurable: true,
    });
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});

    const settled = applyScrollOffsets(null, { scrollTop: 0, windowScrollY: 900 });

    expect(settled).toBe(true);
    expect(scrollTo).toHaveBeenCalledWith(0, 900);
  });

  it('is a no-op when nothing was scrolled', () => {
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    expect(applyScrollOffsets(null, { scrollTop: 0, windowScrollY: 0 })).toBe(true);
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
