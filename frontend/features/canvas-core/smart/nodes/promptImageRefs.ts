// Reading image chips back out of a prompt document.
//
// IC parity (`collectMentionedImagesFromPrompt`): the prompt box owns the
// reference list. A chip sitting in the text IS a reference image — delete the
// chip and the image leaves the run. Keeping the list in the document (rather
// than in a side field the text merely annotates) is what makes that true
// without any extra bookkeeping.

import type { JSONContent } from '@tiptap/core';

/** The tiptap node name for an inline image chip. */
export const PROMPT_IMAGE_REF = 'promptImageRef';

/** An image chip embedded in a prompt body. */
export interface PromptImageRef {
  url: string;
  alias: string;
  kind: string;
}

function walk(node: JSONContent, visit: (n: JSONContent) => void): void {
  visit(node);
  for (const child of node.content ?? []) walk(child, visit);
}

/** Every image chip in the document, in reading order, deduped by url.
 *  A chip with no url is skipped — a reference the generation link could not
 *  use is worse than no reference, because it looks like it worked. */
export function collectImageRefs(doc: JSONContent): PromptImageRef[] {
  const seen = new Set<string>();
  const out: PromptImageRef[] = [];
  walk(doc, (n) => {
    if (n.type !== PROMPT_IMAGE_REF) return;
    const attrs = (n.attrs ?? {}) as Partial<PromptImageRef>;
    const url = typeof attrs.url === 'string' ? attrs.url : '';
    if (!url || seen.has(url)) return;
    seen.add(url);
    out.push({ url, alias: attrs.alias ?? '', kind: attrs.kind ?? 'image' });
  });
  return out;
}

/** The prompt as the model receives it: chips render as `@alias`, block-level
 *  nodes are newline-separated. */
export function docToPromptText(doc: JSONContent): string {
  const blocks: string[] = [];
  for (const block of doc.content ?? []) {
    let line = '';
    walk(block, (n) => {
      if (n.type === PROMPT_IMAGE_REF) {
        const alias = ((n.attrs ?? {}) as Partial<PromptImageRef>).alias ?? '';
        line += `@${alias}`;
      } else if (n.type === 'text') {
        line += n.text ?? '';
      }
    });
    blocks.push(line);
  }
  return blocks.join('\n');
}

/** Matches '@' plus the word characters after it, at the end of the string. */
const AT_BEFORE_CARET = /@(\w*)$/;

/** The active `@query` in the text immediately before the caret, or null when
 *  there is no live mention token there.
 *
 *  This is what makes the picker track typing: '@' opens it, each following
 *  keystroke narrows it, and deleting the '@' returns null — which is the
 *  signal to close. Kept as a pure function of the text so the rule is
 *  testable without driving a contenteditable. */
export function mentionQueryFromText(textBeforeCaret: string): string | null {
  const match = AT_BEFORE_CARET.exec(textBeforeCaret);
  return match ? match[1] : null;
}
