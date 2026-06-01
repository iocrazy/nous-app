/**
 * Publish the real safe-area insets as stable px CSS variables on <html>.
 *
 * Why: on iOS WebKit, `env(safe-area-inset-top)` read inside a freshly-mounted
 * SPA route component can resolve to 0 on the first paint and only update after
 * a scroll/reflow — so content (e.g. the detail-page player + floating back
 * button) renders under the status bar until the user scrolls once.
 *
 * Measuring env() here (during a real full page load, where it is reliable) and
 * exposing it as `--app-safe-top` lets remounting routes read a stable px value
 * via `var(--app-safe-top, env(safe-area-inset-top))` instead of hitting the
 * first-paint-zero bug. We re-measure on resize/orientation changes.
 */

const VAR_TOP = '--app-safe-top';
const VAR_BOTTOM = '--app-safe-bottom';

function measureInset(prop: string): number {
  const probe = document.createElement('div');
  probe.style.cssText = `position:fixed;top:0;left:0;width:0;height:${prop};visibility:hidden;pointer-events:none;`;
  document.body.appendChild(probe);
  const px = probe.getBoundingClientRect().height;
  probe.remove();
  return Number.isFinite(px) ? px : 0;
}

function publishInsets(): void {
  try {
    const top = measureInset('env(safe-area-inset-top)');
    const bottom = measureInset('env(safe-area-inset-bottom)');
    const html = document.documentElement;
    html.style.setProperty(VAR_TOP, `${top}px`);
    html.style.setProperty(VAR_BOTTOM, `${bottom}px`);
  } catch (err) {
    // Non-fatal: callers fall back to env() directly via var() defaults.
    console.error('[safeArea] failed to publish insets', err);
  }
}

/**
 * Install safe-area inset measurement. Call once at app startup.
 * Returns a cleanup function (mainly for tests).
 */
export function installSafeAreaVars(): () => void {
  // Measure after first paint so env() is resolved by WebKit.
  const raf = requestAnimationFrame(publishInsets);

  const onChange = () => publishInsets();
  window.addEventListener('resize', onChange);
  window.addEventListener('orientationchange', onChange);

  return () => {
    cancelAnimationFrame(raf);
    window.removeEventListener('resize', onChange);
    window.removeEventListener('orientationchange', onChange);
  };
}
