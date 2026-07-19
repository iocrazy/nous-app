/**
 * Beat card color strip — a Morandi (muted, low-saturation) preset palette
 * (#1447 lineage). These are data values (stored as hex on the beat row), NOT
 * theme tokens: the card chrome uses `--accent-*` tokens, but the color strip
 * is a user-chosen swatch persisted to `script_beats.color`. Eight presets keep
 * the picker to a single tidy row.
 */
export const BEAT_COLORS: readonly string[] = [
  '#b8a9a0', // clay
  '#c7b9a1', // sand
  '#a9b5a0', // sage
  '#a0b3b8', // mist
  '#a6a9c0', // periwinkle
  '#c0a6b5', // mauve
  '#c9b0a0', // terracotta
  '#aeb0a6', // stone
] as const;

/** Seconds below which a duration reads in seconds rather than minutes. */
const SECONDS_THRESHOLD = 180;

/**
 * Format a beat duration for the card chip: `90s` under 180s, otherwise whole
 * minutes (`10m`). Returns null when there is no duration to show, so callers
 * render nothing.
 */
export function formatBeatDuration(durationSec: number | null): string | null {
  if (durationSec == null || durationSec <= 0) return null;
  if (durationSec < SECONDS_THRESHOLD) return `${durationSec}s`;
  return `${Math.round(durationSec / 60)}m`;
}
