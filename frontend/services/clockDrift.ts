/**
 * Device clock-drift detection (2026-06-11).
 *
 * A device whose clock is far off silently breaks auth: supabase-js
 * schedules token refresh from the LOCAL clock, so a clock weeks behind
 * never refreshes and every request 401s while the UI looks logged-in
 * (root cause of the May-20-clock MacBook incident). The only fix is on
 * the user's machine — so the product must TELL them.
 *
 * Measurement: any HTTP response carries an RFC 7231 `Date` header
 * (1-second resolution — plenty for a 5-minute threshold). We probe our
 * own origin's /version.json (cheap, no auth, no CORS).
 */

export const DRIFT_THRESHOLD_MS = 5 * 60_000;

/** Parse an RFC 7231 Date header into epoch ms, or null. */
export function parseServerDate(header: string | null): number | null {
  if (!header) return null;
  const ms = Date.parse(header);
  return Number.isNaN(ms) ? null : ms;
}

/** drift = localNow - serverNow (negative = local clock behind). */
export function driftExceedsThreshold(driftMs: number, thresholdMs: number): boolean {
  return Math.abs(driftMs) > thresholdMs;
}

/** "22 days" / "3 hours" / "7 minutes" — coarse human units. */
export function formatDrift(driftMs: number): string {
  const abs = Math.abs(driftMs);
  const days = Math.round(abs / 86_400_000);
  if (days >= 1) return days === 1 ? '1 day' : `${days} days`;
  const hours = Math.round(abs / 3_600_000);
  if (hours >= 1) return hours === 1 ? '1 hour' : `${hours} hours`;
  const minutes = Math.max(1, Math.round(abs / 60_000));
  return minutes === 1 ? '1 minute' : `${minutes} minutes`;
}

/**
 * Measure local-vs-server clock drift in ms (local - server).
 * Returns null when it can't be measured (offline, no Date header).
 */
export async function measureClockDrift(): Promise<number | null> {
  try {
    const before = Date.now();
    const res = await fetch('/version.json', { cache: 'no-store' });
    const after = Date.now();
    const serverMs = parseServerDate(res.headers.get('date'));
    if (serverMs === null) return null;
    // Use the midpoint of the request window as "local now" to cancel
    // out network latency (good enough at a 5-minute threshold).
    return Math.round((before + after) / 2) - serverMs;
  } catch {
    return null;
  }
}
