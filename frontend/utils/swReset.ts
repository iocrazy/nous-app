/**
 * Nuke the service worker + all caches, then hard-reload.
 *
 * This is the only reliable escape when a stuck service worker keeps serving an
 * OLD precached app shell (a plain reload is answered from that cache and never
 * upgrades — observed on iOS Safari pinning a tab to an old build across
 * deploys). With no controller and no cache, the next load fetches the fresh
 * build from origin and re-registers a clean SW.
 *
 * Normal updates do NOT need this — they apply silently in the background via
 * the autoUpdate SW (see usePWA). This is the manual "Clear cache & reload"
 * escape exposed in Settings for the rare stuck case.
 */
export async function resetServiceWorkerAndReload(): Promise<void> {
  try {
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister().catch(() => false)));
    }
    if (typeof caches !== 'undefined') {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k).catch(() => false)));
    }
  } catch (err) {
    console.error('[swReset] failed:', err);
  } finally {
    window.location.reload();
  }
}
