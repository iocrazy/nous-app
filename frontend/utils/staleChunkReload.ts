/**
 * Self-healing for stale lazy-route chunks after a redeploy.
 *
 * Cloudflare Pages serves content-hashed bundles and the production alias only
 * points at the newest deployment, so a tab that was opened before a deploy
 * still holds references to chunk filenames that no longer exist. The home page
 * keeps working (its component is already in memory) while every lazy route
 * rejects with "Failed to fetch dynamically imported module" — which reads to
 * the user as "点击不进去".
 *
 * Reloading fixes it (the new index.html points at the new hashes), but the
 * reload itself must be throttled or a genuinely unreachable chunk (offline,
 * origin 500) would loop forever.
 *
 * The previous implementation gated on a write-only sentinel — it stored
 * `Date.now()` but never read it back and never cleared it — so a tab got
 * exactly ONE self-heal for its entire lifetime. After a second deploy every
 * lazy route threw. Time-based throttling gives each deploy a fresh budget
 * while still refusing rapid retries.
 */

/** sessionStorage key holding the epoch ms of the last self-heal reload. */
export const STALE_CHUNK_RELOADED_KEY = 'mh_stale_chunk_reloaded';

/**
 * Minimum gap between two self-heal reloads in one tab.
 *
 * Long enough that a hard-failing chunk cannot spin (a reload + re-navigation
 * costs well under this), short enough that a user who hits two deploys in one
 * session still recovers automatically rather than facing a dead route.
 */
export const RELOAD_THROTTLE_MS = 10_000;

const STALE_CHUNK_PATTERNS = [
  /Failed to fetch dynamically imported module/i,
  /Loading chunk \d+ failed/i,
  /error loading dynamically imported module/i,
];

/** Whether an error message indicates a chunk that no longer exists. */
export function isStaleChunkError(message: string): boolean {
  return STALE_CHUNK_PATTERNS.some((re) => re.test(message));
}

/** Minimal surface of the storage this needs — keeps it unit-testable. */
export interface ReloadSentinelStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * Decide whether to reload now, recording the attempt when the answer is yes.
 *
 * Returns false only when a reload already happened within
 * {@link RELOAD_THROTTLE_MS}. A missing, non-numeric, or future-dated sentinel
 * is treated as "never reloaded" so neither corruption nor clock skew can
 * disable self-healing permanently.
 */
export function shouldReloadForStaleChunk(
  storage: ReloadSentinelStorage,
  now: number,
): boolean {
  const raw = storage.getItem(STALE_CHUNK_RELOADED_KEY);
  const last = raw === null ? NaN : Number(raw);
  const withinWindow =
    Number.isFinite(last) && last <= now && now - last < RELOAD_THROTTLE_MS;

  if (withinWindow) return false;

  storage.setItem(STALE_CHUNK_RELOADED_KEY, String(now));
  return true;
}

/** Minimal surface of the SW container this needs — keeps it unit-testable. */
export interface ServiceWorkerRegistrationLike {
  unregister(): Promise<boolean>;
}
export interface ServiceWorkerContainerLike {
  getRegistrations(): Promise<readonly ServiceWorkerRegistrationLike[]>;
}

/**
 * Reload for self-healing, bypassing the service worker's app shell.
 *
 * A plain ``location.reload()`` is a normal navigation, which workbox's
 * ``navigateFallback`` answers with the PRECACHED index.html. When the SW on
 * this tab still belongs to the PREVIOUS deploy, that "self-heal" lands the
 * user on the old build — old styles, old chunks, all self-consistent — and
 * the tab stays there until the SW happens to update (2026-07-29 field
 * report: clicking a lazy route after a redeploy flipped the whole app back
 * to the pre-palette look). Unregistering every SW first guarantees the
 * reload's navigation hits the network and picks up the newest index.html;
 * the fresh build re-registers its own SW on boot.
 *
 * Best-effort by design: any failure (no SW support, promise rejection, or a
 * hung ``getRegistrations``) still reloads — a stale-shell reload is strictly
 * better than no reload. The 2s timer races the unregister; double reload is
 * harmless (the second fires after navigation started and is a no-op).
 */
export function forceFreshReload(
  reload: () => void = () => window.location.reload(),
  swContainer: ServiceWorkerContainerLike | undefined = typeof navigator !==
  'undefined'
    ? navigator.serviceWorker
    : undefined,
  scheduleFallback: (fn: () => void, ms: number) => void = (fn, ms) => {
    setTimeout(fn, ms);
  },
): void {
  if (!swContainer) {
    reload();
    return;
  }
  let done = false;
  const reloadOnce = () => {
    if (done) return;
    done = true;
    reload();
  };
  try {
    swContainer
      .getRegistrations()
      .then((regs) => Promise.allSettled(regs.map((r) => r.unregister())))
      .then(reloadOnce, reloadOnce);
  } catch {
    reloadOnce();
    return;
  }
  scheduleFallback(reloadOnce, 2000);
}
