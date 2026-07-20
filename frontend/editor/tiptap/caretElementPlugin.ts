/**
 * Marks the element the CARET currently sits in with a `caret-el` node
 * decoration (a class on the NodeView's outer DOM).
 *
 * Why: the legacy engine gave every line its own contentEditable, so
 * `.mh-el-editable:focus` CSS could style "the line being edited" (the firmer
 * placeholder whisper, the dialogue coaching hint). TipTap is ONE
 * contentEditable root — the inner line divs never receive DOM focus and every
 * `:focus`-scoped line rule silently stopped matching. This decoration is the
 * TipTap-engine equivalent hook: style via `.caret-el .mh-el-editable…`.
 *
 * The set is rebuilt from the live selection on every `decorations(state)`
 * call (PM invokes it per transaction), so it needs no plugin state and never
 * drifts. No decoration is emitted when the selection isn't inside a top-level
 * script element (e.g. an all-doc NodeSelection).
 */
import { Extension } from '@tiptap/core';
import { Plugin } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

export const CaretElementExtension = Extension.create({
  name: 'caretElement',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        props: {
          decorations(state) {
            const { $from } = state.selection;
            if ($from.depth < 1) return null;
            const pos = $from.before(1);
            const node = state.doc.nodeAt(pos);
            if (!node) return null;
            return DecorationSet.create(state.doc, [
              Decoration.node(pos, pos + node.nodeSize, { class: 'caret-el' }),
            ]);
          },
        },
      }),
    ];
  },
});
