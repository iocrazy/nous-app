/**
 * usePWA — the service worker must never be registered on the iOS Shortcuts
 * picker (/shortcuts/*). That page opens inside Shortcuts' embedded web view,
 * which closes seconds after the pick: a new Workbox SW never finishes
 * precaching, so an installed old SW keeps serving the old app shell forever.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { renderHook } from '@testing-library/react';

const { registerSW } = vi.hoisted(() => ({ registerSW: vi.fn() }));
vi.mock('virtual:pwa-register', () => ({ registerSW }));

import { shouldRegisterServiceWorker, usePWA } from './usePWA';

describe('shouldRegisterServiceWorker', () => {
  it.each([
    ['/shortcuts/tags', false],
    ['/shortcuts', false],
    ['/shortcuts/', false],
    ['/shortcutsX', true],
    ['/', true],
    ['/t/123/resources', true],
  ])('%s -> %s', (pathname, expected) => {
    expect(shouldRegisterServiceWorker(pathname)).toBe(expected);
  });
});

describe('usePWA', () => {
  const originalPath = window.location.pathname;

  beforeEach(() => {
    registerSW.mockReset();
  });

  afterEach(() => {
    window.history.replaceState(null, '', originalPath);
  });

  it('does not register the service worker on the shortcuts picker', () => {
    window.history.replaceState(null, '', '/shortcuts/tags?token=abc');
    const addSpy = vi.spyOn(document, 'addEventListener');

    const { unmount } = renderHook(() => usePWA());

    expect(registerSW).not.toHaveBeenCalled();
    expect(addSpy).not.toHaveBeenCalledWith('visibilitychange', expect.any(Function));
    unmount();
    addSpy.mockRestore();
  });

  it('registers the service worker on app routes', () => {
    window.history.replaceState(null, '', '/');

    const { unmount } = renderHook(() => usePWA());

    expect(registerSW).toHaveBeenCalledTimes(1);
    expect(registerSW).toHaveBeenCalledWith(expect.objectContaining({ immediate: true }));
    unmount();
  });
});

describe('vite.config workbox navigateFallbackDenylist', () => {
  it('keeps /shortcuts/ navigations off the SW-cached app shell', () => {
    const config = fs.readFileSync(path.resolve(__dirname, '../vite.config.ts'), 'utf8');
    const denylist = config.match(/navigateFallbackDenylist:\s*\[[^\n]*\]/);
    expect(denylist, 'navigateFallbackDenylist not found').not.toBeNull();
    expect(denylist![0]).toContain('/^\\/shortcuts\\//');
  });
});
