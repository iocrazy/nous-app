// frontend/components/Inspiration/tagHighlight.ts
//
// Marks a finished #tag in the composer as a tag while it is being written.
//
// Before this, "#nous" looked like plain text until the note was saved and
// NoteMarkdown rendered it as a chip. The report pointed at Douyin's publish
// box, which sets a #topic apart as soon as it is typed.
//
// Two decisions carry the design:
//
//   PARITY WITH STORAGE. Matches come from tagPattern() — the one boundary
//   rule parseTags() stores by and NoteMarkdown's chip renderer splits by.
//   A highlight that disagreed with storage would promise a tag the note
//   never gets. Code is excluded the same way parseTags() blanks it: code
//   blocks are skipped and inline-code characters are masked.
//
//   PRESENTATION ONLY. This is a ProseMirror decoration, not a mark or node,
//   so the document — and the markdown the composer emits — stays plain
//   "#tag". Nothing about saving, parsing or the autocomplete changes.
//
// A tag the caret sits at the end of is NOT marked: it is still being typed.
// Marking it would flicker on every keystroke and sit on top of the
// autocomplete, which is active in exactly that state. A space, or moving the
// caret away, finishes it.

import { Extension } from '@tiptap/core';
import type { Node as PMNode } from '@tiptap/pm/model';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { EditorState } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { tagPattern } from './noteTags';

export const TAG_CHIP_CLASS = 'note-tag-chip';

const key = new PluginKey('noteTagHighlight');

/** One char per document position inside a textblock, so a string index maps
 *  straight back to a position. Text contributes its characters (inline code
 *  masked to spaces); any other inline leaf (hard break, image) contributes a
 *  single newline — a boundary, like the break it is. */
function flatten(block: PMNode): string {
  let out = '';
  block.forEach((child) => {
    if (child.isText) {
      const text = child.text ?? '';
      out += child.marks.some((m) => m.type.name === 'code') ? ' '.repeat(text.length) : text;
    } else {
      out += '\n'.repeat(child.nodeSize);
    }
  });
  return out;
}

export function tagDecorations(state: EditorState): DecorationSet {
  const { doc, selection } = state;
  const caret = selection.empty ? selection.from : -1;
  const decorations: Decoration[] = [];

  doc.descendants((node, pos) => {
    if (!node.isTextblock) return true;
    if (node.type.spec.code) return false;

    const text = flatten(node);
    const start = pos + 1;
    for (const match of text.matchAll(tagPattern())) {
      const from = start + (match.index ?? 0) + match[1].length;
      const to = from + 1 + match[2].length;
      if (to === caret) continue;
      decorations.push(Decoration.inline(from, to, { class: TAG_CHIP_CLASS }));
    }
    return false;
  });

  return DecorationSet.create(doc, decorations);
}

export const TagHighlight = Extension.create({
  name: 'noteTagHighlight',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key,
        props: { decorations: tagDecorations },
      }),
    ];
  },
});
