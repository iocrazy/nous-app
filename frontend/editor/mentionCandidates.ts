/**
 * Project-level @-mention candidate merge (PR-11, G13). Phase 1 candidates
 * were script-only (the CAST derived from this script's character cues —
 * see `deriveStatistics` in WritingPanel). The workspace shell now also
 * knows the project's OTHER episodes' characters (`GET /projects/{id}/entities`),
 * so a writer on Ep 2 can @-mention a character introduced in Ep 1 without
 * retyping the cue first.
 *
 * Kept pure and dependency-free (no fetch, no React) so EditorShell only has
 * to wire the fetch + `useMemo`; this file is the single place the merge
 * rule is exercised in tests.
 */

/**
 * Merge the current script's CAST names with project-level extras.
 *
 * - The script's own names always come first, in their original (first-seen)
 *   order — they're what the writer is most likely typing in this scene.
 * - Project-level names the script doesn't already have follow, sorted
 *   alphabetically, as reusable extras from other episodes.
 * - Dedup is case-insensitive (`"CLIENT"` and `"Client"` collapse to one
 *   entry, keeping whichever form appeared first).
 */
export function mergeProjectMentionCandidates(
  scriptCandidates: string[],
  projectCandidates: string[],
): string[] {
  const seen = new Set(scriptCandidates.map((name) => name.trim().toUpperCase()));
  const extras = projectCandidates
    .filter((name) => {
      const key = name.trim().toUpperCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => a.localeCompare(b));
  return [...scriptCandidates, ...extras];
}
