/**
 * Publish the real safe-area insets as stable px CSS variables on <html>.
 *
 * Why: on iOS WebKit, `env(safe-area-inset-top)` read inside a freshly-mounted
 * SPA route component can resolve to 0 on the first paint and only update after
 * a scroll/reflow — so content (e.g. the detail-page player + floating back
 * button) renders under the status bar until the user scrolls once.
 *
 * Strategy: measure env() (which is reliable on a persistent probe after a
 * reflow) and, ONLY when we read a non-zero value, publish it as
 * `--app-safe-top` / `--app-safe-bottom`. Consumers use
 * `var(--app-safe-top, env(safe-area-inset-top))`:
 *   - while the var is unset (e.g. before the first reflow), they fall back to
 *     env() directly — same self-healing-on-scroll behaviour as before, no
 *     regression;
 *   - once we capture a real inset, they read a stable px value that survives
 *     SPA route remounts, dodging the first-paint-zero bug.
 *
 * We retry across several timings and re-measure on the same events that
 * historically "healed" the layout (scroll/touch/resize/orientation/visibility)
 * so the real value is captured as early as possible.
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

/**
 * Measure and publish. Only sets a var when we read a non-zero inset, so we
 * never clobber the env() fallback with a spurious 0 (which would disable
 * self-healing on devices/orientations where the inset really is non-zero).
 * Returns true once a non-zero top inset has been captured.
 */
function publishInsets(): boolean {
  try {
    const html = document.documentElement;
    const top = measureInset('env(safe-area-inset-top)');
    const bottom = measureInset('env(safe-area-inset-bottom)');
    let gotTop = false;
    if (top > 0) {
      html.style.setProperty(VAR_TOP, `${top}px`);
      gotTop = true;
    }
    if (bottom > 0) {
      html.style.setProperty(VAR_BOTTOM, `${bottom}px`);
    }
    return gotTop;
  } catch (err) {
    // Non-fatal: consumers fall back to env() directly via var() defaults.
    console.error('[safeArea] failed to publish insets', err);
    return false;
  }
}

/**
 * Install safe-area inset measurement. Call once at app startup.
 * Returns a cleanup function (mainly for tests).
 */
export function installSafeAreaVars(): () => void {
  const timers: number[] = [];

  // Retry across a few timings to catch the frame where WebKit finally
  // resolves env() to a real value after a PWA cold start / route mount.
  const retryDelays = [0, 50, 150, 400, 1000];
  retryDelays.forEach((delay) => {
    timers.push(window.setTimeout(() => publishInsets(), delay));
  });
  const raf = requestAnimationFrame(() => publishInsets());

  // Re-measure on the same events that historically healed the layout, so the
  // real inset is captured the moment it becomes available (and stays put for
  // every subsequent route remount).
  const onChange = () => publishInsets();
  const events: Array<keyof WindowEventMap> = [
    'resize',
    'orientationchange',
    'scroll',
    'touchstart',
    'pageshow',
  ];
  events.forEach((ev) => window.addEventListener(ev, onChange, { passive: true }));
  document.addEventListener('visibilitychange', onChange);

  return () => {
    cancelAnimationFrame(raf);
    timers.forEach((t) => window.clearTimeout(t));
    events.forEach((ev) => window.removeEventListener(ev, onChange));
    document.removeEventListener('visibilitychange', onChange);
  };
}
