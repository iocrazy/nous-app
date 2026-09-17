import { useEffect } from 'react';
import { registerSW } from 'virtual:pwa-register';

// How often to ask the SW to re-check the origin for a new build. iOS Safari
// is lazy about SW updates on its own, which is how a tab gets stuck on an old
// version; forcing update() on register + interval + foreground keeps the
// background auto-update reliable.
const UPDATE_INTERVAL_MS = 30 * 60 * 1000; // 30 min

const SHORTCUTS_PREFIX = '/shortcuts';

/**
 * Is the user in the middle of watching or listening to something?
 *
 * Media segment requests go through the service worker (measured on
 * production: `PerformanceResourceTiming.workerStart > 0` on every HLS
 * segment). `skipWaiting` + `clientsClaim` means a newly discovered build
 * takes over THIS page immediately, and swapping the worker underneath a
 * streaming element is how a tab switch turned into a visible flash: the
 * element re-initialises, and the player then has to seek back to where the
 * viewer was.
 *
 * So the page does not go looking for updates while media is on screen. This
 * only skips OUR polling — the browser still checks on its own schedule and
 * on the next navigation, so an update is delayed, never suppressed.
 *
 * "Paused part-way through" counts as watching: that is exactly the state the
 * reported flash happened in.
 */
export const isMediaActive = (doc: Document = document): boolean => {
  try {
    const els = Array.from(doc.querySelectorAll('video, audio'));
    return els.some((el) => {
      const m = el as HTMLMediaElement;
      return !m.paused || m.currentTime > 0;
    });
  } catch (err) {
    // A broken query must not stop updates forever.
    console.error('[usePWA] media check failed', err);
    return false;
  }
};

/**
 * The iOS Shortcuts picker (/shortcuts/*) opens inside Shortcuts' embedded web
 * view, which is closed seconds after the pick. It needs no offline support,
 * and registering a SW there only leaves a worker behind in that web view's
 * storage. The HTML is served network-fresh and the SW never serves the app
 * shell (see vite.config.ts), so without a SW the picker is always current.
 */
export const shouldRegisterServiceWorker = (pathname: string): boolean =>
  pathname !== SHORTCUTS_PREFIX && !pathname.startsWith(`${SHORTCUTS_PREFIX}/`);

/**
 * Register the service worker and keep it auto-updating in the BACKGROUND.
 *
 * registerType is 'autoUpdate' (skipWaiting + clientsClaim, see vite.config),
 * so a freshly installed build takes over on the next navigation/relaunch —
 * no prompt, no banner. We just make sure the SW actually re-checks for new
 * builds (immediately, every 30 min, and whenever the tab returns to the
 * foreground) so it never gets pinned to a stale version. The manual
 * "Clear cache & reload" escape for an already-stuck SW lives in Settings
 * (see utils/swReset).
 *
 * Skipped entirely on /shortcuts/* (see shouldRegisterServiceWorker). This
 * does NOT unregister an existing SW — the same origin's main PWA shares it.
 */
/** `reg.update()`, unless the user is watching something — see `isMediaActive`. */
const maybeUpdate = (reg: ServiceWorkerRegistration): void => {
  if (isMediaActive()) return;
  reg.update().catch(() => {});
};

export function usePWA() {
  useEffect(() => {
    // Read once at mount: the picker is a standalone page, never navigated to
    // client-side from the app.
    if (!shouldRegisterServiceWorker(window.location.pathname)) return;

    let reg: ServiceWorkerRegistration | undefined;
    let timer: ReturnType<typeof setInterval> | undefined;

    registerSW({
      immediate: true,
      onRegisteredSW(_swUrl, r) {
        reg = r ?? undefined;
        if (!reg) return;
        maybeUpdate(reg);
        timer = setInterval(() => {
          if (reg) maybeUpdate(reg);
        }, UPDATE_INTERVAL_MS);
      },
    });

    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      if (reg) maybeUpdate(reg);
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);
}
