// Flip the Nth (document-order, 0-based) GFM task-list checkbox in markdown
// source. Fenced code blocks are excluded so a `- [ ]` shown as a code sample
// isn't counted. Contract mirrored by NoteMarkdown's checkbox index (see
// taskMarkers.ts, which owns the shared offset-scanning logic).
import { computeTaskOffsets } from './taskMarkers';

export function toggleTaskItem(md: string, index: number): string {
  const start = computeTaskOffsets(md)[index];
  if (start === undefined) return md;
  const ch = md[start];
  const next = ch === ' ' ? 'x' : ' ';
  return md.slice(0, start) + next + md.slice(start + 1);
}
