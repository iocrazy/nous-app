// Mirror of backend/app/services/inspiration/note_tags.py — keep the two in
// lockstep (change one → change the other → update the spec §2.4).
// Rules: '#' preceded by start/whitespace/'('/'（'; body = unicode letters,
// digits, '-', '_'; '# ' (heading) is not a tag; fenced/inline code stripped;
// output lowercased (ASCII), deduped, first-seen order.

const CODE_FENCE = /```[\s\S]*?```/g;
const INLINE_CODE = /`[^`\n]*`/g;
const TAG = /(^|[\s(（])#([\p{L}\p{N}_-]+)/gu;

export function parseTags(contentMd: string): string[] {
  if (!contentMd) return [];
  const text = contentMd.replace(CODE_FENCE, ' ').replace(INLINE_CODE, ' ');
  const seen = new Set<string>();
  const out: string[] = [];
  for (const match of text.matchAll(TAG)) {
    const tag = match[2].toLowerCase();
    if (tag && !seen.has(tag)) {
      seen.add(tag);
      out.push(tag);
    }
  }
  return out;
}

/** While typing in the composer: the #token the caret is currently inside. */
export function findActiveTag(
  text: string,
  caret: number,
): { start: number; prefix: string } | null {
  const upto = text.slice(0, caret);
  const hash = upto.lastIndexOf('#');
  if (hash === -1) return null;
  const before = hash === 0 ? '' : upto[hash - 1];
  if (before && !/[\s(（]/.test(before)) return null;
  const token = upto.slice(hash + 1);
  if (token.length === 0) return { start: hash, prefix: '' };
  if (!/^[\p{L}\p{N}_-]+$/u.test(token)) return null;
  return { start: hash, prefix: token.toLowerCase() };
}
