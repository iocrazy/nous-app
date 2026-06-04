import { useEffect } from 'react';
import { registerSW } from 'virtual:pwa-register';

// How often to ask the SW to re-check the origin for a new build. iOS Safari
// is lazy about SW updates on its own, which is how a tab gets stuck on an old
// version; forcing update() on register + interval + foreground keeps the
// background auto-update reliable.
const UPDATE_INTERVAL_MS = 30 * 60 * 1000; // 30 min

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
 */
export function usePWA() {
  useEffect(() => {
    let reg: ServiceWorkerRegistration | undefined;
    let timer: ReturnType<typeof setInterval> | undefined;

    registerSW({
      immediate: true,
      onRegisteredSW(_swUrl, r) {
        reg = r ?? undefined;
        if (!reg) return;
        reg.update().catch(() => {});
        timer = setInterval(() => {
          reg?.update().catch(() => {});
        }, UPDATE_INTERVAL_MS);
      },
    });

    const onVisible = () => {
      if (document.visibilityState === 'visible') reg?.update().catch(() => {});
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);
}
