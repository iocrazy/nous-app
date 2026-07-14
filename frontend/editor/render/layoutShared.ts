/**
 * Shared row helpers for the TipTap editing surface.
 *
 * Once the legacy contentEditable layout engines were retired, the only
 * survivors here are the pure helpers the TipTap NodeView + mention decoration
 * plugin still consume: `scanMentionRuns` (the single source of the `@name`
 * chip ranges — the decoration plugin lays inline `Decoration`s over exactly
 * these spans without ever mutating text) and `elementEdgeFromPointer` (the
 * drag-to-reorder drop-edge test). No React, no DOM writes — just functions.
 */

// The default (unknown-name) mention token: `@` + a run of non-space, non-`@`
// chars. A KNOWN multi-word CAST name overrides this and chips as a whole (see
// matchKnownName); an unknown name still stops at the first word.
const MENTION_WORD_RE = /^[^\s@]+/;

/**
 * The longest known CAST name that `after` starts with (case-insensitive) on a
 * word boundary — this is what lets `@John Smith` chip as one token when
 * "John Smith" is a known name. Returns the ORIGINAL-cased slice, or null.
 */
function matchKnownName(after: string, knownLongestFirst: string[]): string | null {
  const lower = after.toLowerCase();
  for (const cand of knownLongestFirst) {
    if (cand.length === 0) continue;
    if (lower.startsWith(cand.toLowerCase())) {
      const next = after.charAt(cand.length);
      // Boundary: end of line, or a non-alphanumeric follows (so "John Smith"
      // matches "@John Smith!" but not "@John Smithers").
      if (next === '' || !/[A-Za-z0-9]/.test(next)) {
        return after.slice(0, cand.length);
      }
    }
  }
  return null;
}

/** One `@name` run found in a plain-text string — a character-offset span
 *  (`[start, end)`, `start` at the `@`) plus the matched name and whether it
 *  is a known CAST name. The TipTap mention decoration plugin (M3) lays inline
 *  `Decoration`s over exactly these ranges, never mutating text. */
export interface MentionRun {
  /** Character offset of the `@`. */
  start: number;
  /** Character offset one past the matched name (exclusive). */
  end: number;
  /** The matched name, original casing, WITHOUT the leading `@`. */
  name: string;
  isKnown: boolean;
}

/**
 * Scan `text` for `@name` runs. A name present in `mentionNames`
 * (case-insensitive) is a known run — matched greedily so multi-word CAST
 * names (`@John Smith`) chip whole; an unknown name still chips, but stops at
 * the first word (never an error — an in-progress `@partial` still gets a
 * (grey) chip while typing). A lone `@` with nothing after it is not a run.
 */
export function scanMentionRuns(text: string, mentionNames: string[]): MentionRun[] {
  const known = new Set(mentionNames.map((n) => n.toLowerCase()));
  // Longest-first so a multi-word name wins over a shorter one it contains.
  const knownLongestFirst = [...mentionNames].sort((a, b) => b.length - a.length);
  const runs: MentionRun[] = [];
  let i = 0;
  while (i < text.length) {
    const at = text.indexOf('@', i);
    if (at === -1) break;
    const after = text.slice(at + 1);
    let name = matchKnownName(after, knownLongestFirst);
    let isKnown = name != null;
    if (name == null) {
      const m = after.match(MENTION_WORD_RE);
      name = m ? m[0] : '';
      isKnown = name.length > 0 && known.has(name.toLowerCase());
    }
    if (name.length === 0) {
      // A lone `@` with nothing after it — not a run, keep scanning past it.
      i = at + 1;
      continue;
    }
    runs.push({ start: at, end: at + 1 + name.length, name, isKnown });
    i = at + 1 + name.length;
  }
  return runs;
}

/** Which half of an element row the pointer is over → the drop edge. 'top'
 *  lands the dragged element BEFORE this row, 'bottom' lands it AFTER. */
export function elementEdgeFromPointer(el: HTMLElement, clientY: number): 'top' | 'bottom' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'top' : 'bottom';
}
