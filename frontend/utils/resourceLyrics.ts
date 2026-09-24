/**
 * `resources.lyrics_json` is a JSONB column, so the resources API types it as
 * an open object (`{ [key: string]: unknown } | null`). The backend's LRC
 * parser writes `{ lrc, lines: [{ text, line_start_ms }] }`; this module is
 * the one place that narrows the open object to that shape.
 */

// Type aliases, not interfaces: an alias is assignable back into the open
// wire type (`{ [key: string]: unknown }`) when a row is patched locally.
export type ResourceLyricLine = {
  text: string;
  line_start_ms: number | null;
};

export type ResourceLyrics = {
  lrc: string;
  lines: ResourceLyricLine[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function toLyricLine(value: unknown): ResourceLyricLine | null {
  if (!isRecord(value) || typeof value.text !== 'string') return null;
  const start = value.line_start_ms;
  return {
    text: value.text,
    line_start_ms: typeof start === 'number' && Number.isFinite(start) ? start : null,
  };
}

/** Narrow a wire `lyrics_json` value. Returns null for null / non-object
 *  input; malformed lines are dropped rather than rendered as blanks. */
export function parseResourceLyrics(value: unknown): ResourceLyrics | null {
  if (!isRecord(value)) return null;
  const rawLines = Array.isArray(value.lines) ? value.lines : [];
  const lines = rawLines
    .map(toLyricLine)
    .filter((line): line is ResourceLyricLine => line !== null);
  return { lrc: typeof value.lrc === 'string' ? value.lrc : '', lines };
}
