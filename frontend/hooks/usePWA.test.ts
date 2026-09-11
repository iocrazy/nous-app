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

describe('vite.config workbox: navigations are network-only, precache is tiny', () => {
  // Pin code, not prose: whole-line comments may name the old options.
  const code = fs
    .readFileSync(path.resolve(__dirname, '../vite.config.ts'), 'utf8')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n');

  it('disables navigateFallback explicitly — the SW never answers a navigation with a cached shell', () => {
    // Omitting the key is NOT enough: vite-plugin-pwa defaults it to
    // 'index.html', which registers a precached-shell NavigationRoute ahead of
    // every runtime rule (and throws at SW startup once index.html is no
    // longer precached). Only an explicit null turns it off.
    expect(code).toMatch(/\bnavigateFallback:\s*null\s*,/);
    expect(code).not.toMatch(/\bnavigateFallback(Allow|Deny)list\s*:/);
  });

  it('routes navigations NetworkOnly with an /offline.html fallback as the first runtime rule', () => {
    const start = code.indexOf('runtimeCaching:');
    expect(start, 'runtimeCaching not found').toBeGreaterThanOrEqual(0);
    const runtime = code.slice(start);
    const rules = runtime.split(/\burlPattern\s*:/).slice(1);
    expect(rules.length, 'no runtimeCaching rules').toBeGreaterThan(0);
    const first = rules[0];
    expect(first).toMatch(/^\s*\(\{\s*request\s*\}\)\s*=>\s*request\.mode\s*===\s*'navigate'/);
    expect(first).toMatch(/handler:\s*'NetworkOnly'/);
    expect(first).toMatch(/precacheFallback:\s*\{\s*fallbackURL:\s*'\/offline\.html'\s*\}/);
  });

  it('precaches only small static files — no index.html, no JS/CSS bundles', () => {
    const match = code.match(/globPatterns:\s*\[([^\]]*)\]/);
    expect(match, 'globPatterns not found').not.toBeNull();
    const patterns = [...match![1].matchAll(/'([^']*)'/g)].map((m) => m[1]);
    expect(patterns).toContain('offline.html');
    for (const pattern of patterns) {
      expect(pattern).not.toMatch(/index\.html|assets|\bjs\b|\bcss\b|\*\.html|\{[^}]*html/);
    }
  });
});
