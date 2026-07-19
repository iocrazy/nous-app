// Mirror of backend/app/services/inspiration/note_tags.py — keep the two in
// lockstep (change one → change the other → update the spec §2.4).
// Rules: '#' preceded by start/whitespace/'('/'（'; body = unicode letters,
// digits, '-', '_'; '# ' (heading) is not a tag; fenced/inline code stripped;
// output lowercased (ASCII), deduped, first-seen order.

const CODE_FENCE = /```[\s\S]*?```/g;
const INLINE_CODE = /`[^`\n]*`/g;

/** The one boundary rule shared by parseTags() (what gets stored) and the
 *  inline-chip renderer (what gets highlighted). Returned fresh each call
 *  because a /g regex carries mutable `lastIndex`; sharing a single instance
 *  across exec()/matchAll loops corrupts iteration. Change here → change
 *  backend note_tags.py → update the spec §2.4. */
export function tagPattern(): RegExp {
  return /(^|[\s(（])#([\p{L}\p{N}_-]+)/gu;
}

export function parseTags(contentMd: string): string[] {
  if (!contentMd) return [];
  const text = contentMd.replace(CODE_FENCE, ' ').replace(INLINE_CODE, ' ');
  const seen = new Set<string>();
  const out: string[] = [];
  for (const match of text.matchAll(tagPattern())) {
    const tag = match[2].toLowerCase();
    if (tag && !seen.has(tag)) {
      seen.add(tag);
      out.push(tag);
    }
  }
  return out;
}

export type TagPart =
  | { type: 'text'; value: string }
  | { type: 'tag'; raw: string; value: string };

/** Split one plain-text run into interleaved text/tag segments using the SAME
 *  boundary rule parseTags() stores by, so a rendered chip appears for exactly
 *  the substrings that became stored tags. `raw` keeps the source casing (for
 *  display); `value` is lowercased (for filtering), matching parseTags(). The
 *  leading boundary char (whitespace / '(' captured by the pattern) stays in
 *  the text stream — only `#tag` itself becomes a tag part. Callers must not
 *  feed code/pre text here; that stripping is structural (via the AST), the
 *  mirror of parseTags() blanking fenced/inline code first. */
export function splitTagParts(text: string): TagPart[] {
  const parts: TagPart[] = [];
  const re = tagPattern();
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const lead = match[1]; // '' | whitespace | '(' | '（'
    const raw = match[2];
    const hashStart = match.index + lead.length;
    if (hashStart > last) parts.push({ type: 'text', value: text.slice(last, hashStart) });
    parts.push({ type: 'tag', raw, value: raw.toLowerCase() });
    last = re.lastIndex;
  }
  if (last < text.length) parts.push({ type: 'text', value: text.slice(last) });
  return parts;
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
