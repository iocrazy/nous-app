/**
 * Character-name matching for the "script cast vs. character library" diff.
 *
 * Why this exists: the script side (`GET /api/v1/projects/{id}/entities`,
 * derived from every non-deleted script's scenes) and the library side
 * (`project_characters` rows) are two independent name spaces that only ever
 * meet by string. `POST /characters/extract` upserts with
 * `ON CONFLICT (project_id, name) DO NOTHING`, i.e. the *server* matches
 * byte-exactly. This module deliberately matches a bit *wider* than the
 * server so the hint stays quiet in the ambiguous cases:
 *
 *   normalize(name) = fullwidth→halfwidth → trim → collapse inner whitespace
 *                     → lowercase
 *
 * Consequences of matching wider (intentional, both directions considered):
 *  - Library has `alice`, script has `Alice` → we treat it as already present,
 *    so no banner. Importing would have created a *second*, near-duplicate row
 *    (the server's unique index is exact), so staying quiet is the safe side.
 *  - We never *hide* a genuinely new name: normalization only ever merges
 *    names that differ by case/width/whitespace, never distinct spellings.
 *
 * The banner count is therefore an upper-bound-free, conservative estimate of
 * "names the script knows that the library has no card for".
 */

/** Fullwidth ASCII (U+FF01–U+FF5E) → ASCII, ideographic space → plain space. */
function toHalfWidth(input: string): string {
  return input
    .replace(/[！-～]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/　/g, ' ');
}

/** Canonical key a script name and a library name are compared by. */
export function normalizeCharacterName(name: string): string {
  return toHalfWidth(name).trim().replace(/\s+/g, ' ').toLowerCase();
}

/**
 * Script cast names that have no card in the library yet, in script order,
 * de-duplicated by normalized key. Blank/whitespace-only names are dropped
 * (the server drops them too — `upsert_by_name` filters `n.strip()`).
 */
export function missingCharacterNames(
  scriptNames: readonly string[],
  libraryNames: readonly string[],
): string[] {
  const present = new Set(
    libraryNames.map(normalizeCharacterName).filter((k) => k.length > 0),
  );
  const out: string[] = [];
  for (const raw of scriptNames) {
    const key = normalizeCharacterName(raw ?? '');
    if (!key || present.has(key)) continue;
    present.add(key); // de-dupe within the script list itself
    out.push(raw.trim());
  }
  return out;
}
