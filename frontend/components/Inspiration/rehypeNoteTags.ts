// frontend/components/Inspiration/rehypeNoteTags.ts
// Rehype plugin: rewrite `#tag` runs in note-body text into <notetag> element
// nodes so NoteMarkdown can render them as inline chips (spec §2.2 tag chips).
//
// MUST run BEFORE rehype-highlight. It skips <code>/<pre> subtrees so a
// `#word` inside a fence or inline code is never chipped — and *before*
// highlight those subtrees still hold their raw code text directly under
// <code>; highlight later tokenizes code into nested <span>s, which would
// slip past the skip. Boundary parity with the stored tags comes entirely
// from splitTagParts (shared with parseTags), never a second regex.
import { splitTagParts } from './noteTags';

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

const SKIP_SUBTREE = new Set(['code', 'pre']);

/** Rebuild a node's children, expanding text runs into text + <notetag>
 *  segments. Returns a new children array (no in-place child mutation) and
 *  recurses into element children except code/pre. */
function rewrite(node: HastNode): void {
  const children = node.children;
  if (!children || children.length === 0) return;

  const next: HastNode[] = [];
  for (const child of children) {
    if (child.type === 'text' && typeof child.value === 'string') {
      const parts = splitTagParts(child.value);
      if (parts.length === 1 && parts[0].type === 'text') {
        next.push(child);
        continue;
      }
      for (const part of parts) {
        if (part.type === 'text') {
          next.push({ type: 'text', value: part.value });
        } else {
          next.push({
            type: 'element',
            tagName: 'notetag',
            properties: { 'data-tag': part.value },
            children: [{ type: 'text', value: part.raw }],
          });
        }
      }
    } else if (child.type === 'element' && child.tagName && SKIP_SUBTREE.has(child.tagName)) {
      next.push(child);
    } else {
      rewrite(child);
      next.push(child);
    }
  }
  node.children = next;
}

export function rehypeNoteTags() {
  return (tree: HastNode): void => {
    rewrite(tree);
  };
}
