/**
 * Nuke the service worker + all caches, then hard-reload.
 *
 * This is the only reliable escape when a stuck service worker keeps serving an
 * OLD precached app shell (a plain reload is answered from that cache and never
 * upgrades — observed on iOS Safari pinning a tab to an old build across
 * deploys). With no controller and no cache, the next load fetches the fresh
 * build from origin and re-registers a clean SW.
 *
 * Both callers (Settings "Clear Cache & Reload" and the VersionBadge
 * "update to vX" link) go through here, so the full-screen "Updating…" overlay
 * lives here once — the user gets clear feedback that the upgrade is running
 * instead of a frozen button + blank flash.
 */

const OVERLAY_ID = 'sw-reset-overlay';

function showUpdatingOverlay(): void {
  if (typeof document === 'undefined' || document.getElementById(OVERLAY_ID)) {
    return;
  }
  const el = document.createElement('div');
  el.id = OVERLAY_ID;
  el.setAttribute('role', 'status');
  el.setAttribute('aria-live', 'polite');
  el.style.cssText = [
    'position:fixed',
    'inset:0',
    'z-index:2147483647',
    'display:flex',
    'flex-direction:column',
    'align-items:center',
    'justify-content:center',
    'gap:16px',
    'background:rgba(9,9,11,0.94)',
    '-webkit-backdrop-filter:blur(4px)',
    'backdrop-filter:blur(4px)',
    'color:#fafafa',
    'font-family:Inter,ui-sans-serif,system-ui,sans-serif',
    'font-size:14px',
    'letter-spacing:0.01em',
    // Belt-and-suspenders: don't let a stuck previous reload leave it dismissable
    'pointer-events:all',
  ].join(';');
  el.innerHTML =
    '<div style="width:36px;height:36px;border:3px solid #3f3f46;' +
    'border-top-color:#6366f1;border-radius:9999px;' +
    'animation:swSpin .7s linear infinite"></div>' +
    '<div>Updating…</div>' +
    '<style>@keyframes swSpin{to{transform:rotate(360deg)}}</style>';
  document.body.appendChild(el);
}

export async function resetServiceWorkerAndReload(): Promise<void> {
  showUpdatingOverlay();
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
