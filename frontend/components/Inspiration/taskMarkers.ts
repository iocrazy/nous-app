// Shared "document order index of the Nth GFM task-checkbox" contract between
// toggleTaskItem (mutates markdown source) and NoteMarkdown (renders it).
// Both must agree on indexing so a click on the Nth rendered checkbox always
// flips the Nth marker in source. Fenced code blocks are masked out (replaced
// with same-length filler) so a `- [ ]` shown as a code sample isn't counted,
// while every other character keeps its original offset.
const FENCE = /```[\s\S]*?```/g;
const TASK = /^(\s*[-*+] \[)([ xX])(\])/gm;

/** Offsets (into the original, unmasked source) of the checkbox content char
 *  (the space/x/X between the brackets) for each GFM task-list marker, in
 *  document order. */
export function computeTaskOffsets(source: string): number[] {
  const masked = source.replace(FENCE, (m) => ' '.repeat(m.length));
  const offsets: number[] = [];
  for (const m of masked.matchAll(TASK)) {
    offsets.push((m.index ?? 0) + m[1].length);
  }
  return offsets;
}

export { FENCE, TASK };
