/**
 * Per-media playback position, remembered across reloads.
 *
 * Why: a deploy swaps the service worker (`registerType: 'autoUpdate'` →
 * skipWaiting + clientsClaim) and a stale chunk can trigger
 * `staleChunkReload`. Either way the tab re-mounts mid-video, and until now
 * playback restarted at 00:00 — a 20-minute review lost its place because a
 * build shipped.
 *
 * Design notes:
 * - The key must survive the thing that caused the reload, so it is derived
 *   from the media's IDENTITY, never from the URL as handed to `<video>`:
 *   those carry a short-lived `?token=` and switch path between the HLS
 *   playlist and the original file when quality changes. Two spellings of the
 *   same video must not get two positions.
 * - Storage is capped and pruned. A viewer who opens hundreds of clips would
 *   otherwise grow this forever, and localStorage failure is silent-ish (a
 *   quota throw at an unrelated call site is a miserable way to find out).
 * - Every accessor swallows storage errors: private windows and blocked site
 *   data must degrade to "no memory", never to a broken player.
 */

const STORE_KEY = 'mediahub_playback_positions_v1';

/** Positions older than this are dropped on the next write. */
export const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

/** Hard cap on remembered entries; the oldest go first. */
export const MAX_ENTRIES = 200;

/**
 * Below this, resuming is worse than starting over — the viewer has barely
 * begun and a 3-second offset just feels like a glitch.
 */
export const MIN_RESUME_SECONDS = 5;

/**
 * How close to the end counts as "finished". Resuming 2 seconds before the
 * credits means the next open immediately hits `ended`.
 */
export const END_MARGIN_SECONDS = 10;

export interface PlaybackPosition {
  /** Seconds into the media. */
  t: number;
  /** Duration when the position was stored, for the end-margin check. */
  d: number;
  /** Epoch ms of the last write, for pruning. */
  ts: number;
}

type Store = Record<string, PlaybackPosition>;

function readStore(): Store {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed as Store;
  } catch (err) {
    console.error('[playbackResume] read failed', err);
    return {};
  }
}

function writeStore(store: Store): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch (err) {
    console.error('[playbackResume] write failed', err);
  }
}

/** Drop expired entries, then the oldest ones above {@link MAX_ENTRIES}. */
export function prune(store: Store, now: number = Date.now()): Store {
  const live = Object.entries(store).filter(
    ([, v]) => v && typeof v.ts === 'number' && now - v.ts <= MAX_AGE_MS,
  );
  live.sort((a, b) => b[1].ts - a[1].ts); // newest first
  return Object.fromEntries(live.slice(0, MAX_ENTRIES));
}

/**
 * Stable per-media key.
 *
 * `explicit` is what a caller that knows the resource/media id should pass.
 * The fallback strips the query string (the `?token=` is rotated on every
 * session) and keeps only the path, which is the best identity available when
 * a caller has nothing better.
 */
export function resumeKeyFor(explicit: string | undefined, src: string): string {
  if (explicit && explicit.trim()) return explicit.trim();
  try {
    return new URL(src, window.location.origin).pathname;
  } catch {
    // A src that URL() refuses is still a usable key as long as we cut the
    // query ourselves — better a coarse key than no memory at all.
    return src.split('?')[0];
  }
}

/** Store the position for `key`. A no-op for positions not worth resuming. */
export function savePosition(key: string, t: number, d: number): void {
  if (!key || !Number.isFinite(t) || !Number.isFinite(d) || d <= 0) return;
  if (t < MIN_RESUME_SECONDS || t > d - END_MARGIN_SECONDS) {
    // Too early or effectively finished — and crucially, CLEAR any older
    // position. Without this, watching to the end then reopening would jump
    // back to the stale midpoint.
    clearPosition(key);
    return;
  }
  const store = prune(readStore());
  store[key] = { t, d, ts: Date.now() };
  writeStore(prune(store));
}

/** The resumable position for `key`, or null. */
export function loadPosition(key: string, duration?: number): number | null {
  if (!key) return null;
  const entry = readStore()[key];
  if (!entry || !Number.isFinite(entry.t)) return null;
  const d = Number.isFinite(duration) && (duration as number) > 0 ? (duration as number) : entry.d;
  if (!Number.isFinite(d) || d <= 0) return null;
  if (entry.t < MIN_RESUME_SECONDS || entry.t > d - END_MARGIN_SECONDS) return null;
  return entry.t;
}

export function clearPosition(key: string): void {
  if (!key) return;
  const store = readStore();
  if (!(key in store)) return;
  delete store[key];
  writeStore(store);
}
