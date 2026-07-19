/**
 * Beats M3 — target-length parsing/formatting for the Arrangement topbar control
 * and the template wizard. Pure, DOM-free, separately tested.
 *
 * Display uses the film apostrophe: whole minutes read `45'`, minutes+seconds
 * read `1'30`, sub-minute reads `45s`. Custom input accepts either whole seconds
 * (`2700`) or an `m:ss` clock (`45:00`) — both resolve to a second count.
 */

/** PG INTEGER ceiling — the column the target persists to (mig 376). */
const MAX_TARGET_SEC = 2_147_483_647;

/** Topbar quick-picks (issue #1471): 60s · 3' · 20' · 45' · 90'. */
export const DURATION_PRESETS: readonly number[] = [60, 180, 1200, 2700, 5400] as const;

/** `45'` / `1'30` / `45s` — the compact runtime label. */
export function formatTargetLength(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s === 0 ? `${m}'` : `${m}'${String(s).padStart(2, '0')}`;
}

/**
 * Parse a custom length string into whole seconds, or null when it is empty /
 * malformed / out of range. Accepts `"2700"` (seconds) or `"45:00"` (`m:ss`,
 * seconds field 0–59). Always ≥ 1 and ≤ the PG INTEGER ceiling.
 */
export function parseDurationInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === '') return null;
  let total: number;
  if (trimmed.includes(':')) {
    const parts = trimmed.split(':');
    if (parts.length !== 2) return null;
    const m = Number(parts[0]);
    const s = Number(parts[1]);
    if (!Number.isInteger(m) || !Number.isInteger(s) || m < 0 || s < 0 || s > 59) return null;
    total = m * 60 + s;
  } else {
    const n = Number(trimmed);
    if (!Number.isInteger(n)) return null;
    total = n;
  }
  if (total < 1 || total > MAX_TARGET_SEC) return null;
  return total;
}
