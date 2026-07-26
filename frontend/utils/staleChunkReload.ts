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
