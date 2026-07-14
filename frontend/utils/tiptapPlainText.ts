// Byte-preserving bridge between a raw text string and a single-code-block
// TipTap document (spec 2026-07-14). The whole point: NEVER route
// non-markdown text through the markdown parser/serializer. A code_block
// node stores its content as literal text (newlines are real \n characters,
// whitespace: 'pre'), so building the doc directly and reading text nodes
// back is a lossless round-trip.

import type { JSONContent } from '@tiptap/core';

export function plainTextToDoc(
  text: string,
  language?: string | null,
): JSONContent {
  const codeBlock: JSONContent = {
    type: 'codeBlock',
    attrs: { language: language ?? null },
    // An empty string must produce an empty code block (no text node), or
    // ProseMirror rejects a zero-length text node.
    content: text ? [{ type: 'text', text }] : [],
  };
  return { type: 'doc', content: [codeBlock] };
}

export function docToPlainText(doc: JSONContent): string {
  const block = doc.content?.[0];
  if (!block || block.type !== 'codeBlock' || !block.content) return '';
  // Concatenate every text node's text verbatim — newlines live inside these
  // text nodes for a code block, so this is byte-exact.
  return block.content
    .map((n) => (n.type === 'text' ? n.text ?? '' : ''))
    .join('');
}
