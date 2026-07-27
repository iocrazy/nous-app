/**
 * Comma-separated `options` text <-> string[] conversion for the template
 * editor's Form tab (M3 PR-I §2) `select` field row. Extracted as pure
 * functions (no React) so the parse/format edge cases — blank entries,
 * surrounding whitespace, an empty draft — are covered by cheap vitest
 * instead of only exercised indirectly through the editor's e2e (I4).
 */

/** Free-text "a, b, c" -> `['a', 'b', 'c']`. Blank segments (leading/trailing
 * commas, double commas, whitespace-only input) are dropped rather than kept
 * as empty-string options — an empty option would render a picker entry with
 * nothing to pick. */
export function parseOptionsInput(raw: string): string[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** `['a', 'b']` -> `"a, b"` for populating the text input. `undefined`/empty
 * renders as an empty string (a fresh `select` field before its first
 * option is typed). */
export function formatOptionsInput(options: string[] | undefined): string {
  return (options ?? []).join(', ');
}
