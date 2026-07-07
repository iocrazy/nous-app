// Flip the Nth (document-order, 0-based) GFM task-list checkbox in markdown
// source. Fenced code blocks are excluded so a `- [ ]` shown as a code sample
// isn't counted. Contract mirrored by NoteMarkdown's checkbox index counter.
const FENCE = /```[\s\S]*?```/g;
const TASK = /^(\s*[-*+] \[)([ xX])(\])/gm;

export function toggleTaskItem(md: string, index: number): string {
  // Mask fenced code so task markers inside it are neither counted nor edited,
  // but keep exact character offsets by replacing with same-length filler.
  const masked = md.replace(FENCE, (m) => ' '.repeat(m.length));
  let seen = -1;
  let hit: { start: number; ch: string } | null = null;
  for (const m of masked.matchAll(TASK)) {
    seen += 1;
    if (seen === index) {
      // offset of the [ ]/[x] char = match index + length of group 1
      hit = { start: (m.index ?? 0) + m[1].length, ch: m[2] };
      break;
    }
  }
  if (!hit) return md;
  const next = hit.ch === ' ' ? 'x' : ' ';
  return md.slice(0, hit.start) + next + md.slice(hit.start + 1);
}
