/**
 * Paged mode v2's element-level seams (spec D5/M3 item 2) as a ProseMirror
 * widget-decoration plugin. `EditorShell`'s measurement effect
 * (`components/EditorShell.tsx` ~L752-831) already computes the SAME
 * `Map<elementId, {page, filler}>` the legacy layout engines splice a
 * `<PageSeam>` before (`render/PageSeam.tsx`, `render/HollywoodLayout.tsx` /
 * `AsianLayout.tsx`); this plugin renders the IDENTICAL DOM as a widget
 * immediately before the matching `scriptElement` node's own DOM, so the
 * shell's `.mh-page-seam` subtraction math (it walks `.mh-el-row` boxes and
 * subtracts any `.mh-page-seam` box above them to get stable content
 * coordinates) works unchanged — the widget is a plain descendant of the
 * measured `sheet` container regardless of PM's internal DOM nesting.
 *
 * Scene-level seams (`scene:<id>` keys, the heading row) are NOT this
 * plugin's concern — they stay exactly as `EditorShell` already renders them
 * (untouched by the TipTap migration; only element-level seams move into the
 * editing surface).
 *
 * Design: the plugin holds no doc-independent state of its own. `decorations`
 * rebuilds the `DecorationSet` straight from `state.doc` + the live
 * `pageSeamsRef` on every call (cheap — one pass over the doc's top-level
 * children, no regex/text scanning). PM calls `decorations(state)` after
 * EVERY transaction, so a doc edit (docChanged) picks up new seam positions
 * automatically; a `pageSeams` PROP change with no doc edit of its own is
 * forced through by `TipTapSceneEditor`'s existing `propsSync` no-op
 * transaction (see its module doc — `pageSeams` was added to that effect's
 * dependency array), which is enough to trigger a re-call.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import type { Node as PMNode } from '@tiptap/pm/model';
import type { EditorState } from '@tiptap/pm/state';

export interface PageSeamEntry {
  page: number;
  filler: number;
}
export type PageSeamMap = Map<string, PageSeamEntry>;

export const pageSeamPluginKey = new PluginKey<null>('pageSeamDecorations');

/** Builds the SAME DOM `PageSeam.tsx` renders (kept in sync: class names,
 *  `data-testid`, the `filler` → `paddingTop` style, the bare-integer page
 *  number nested in the dashed rule) — imperative `document.createElement`
 *  because PM widget decorations render outside React's tree. */
function seamDom(entry: PageSeamEntry): HTMLElement {
  const el = document.createElement('div');
  // `mh-page-seam-inline` marks the IN-EDITOR context: this widget renders inside
  // `.mh-scene-block` (which has `padding-left:4px`), whereas the scene-level
  // `<PageSeam>` component is a direct child of `.mh-sheet-inner` with no such
  // inset. The shared negative left margin is tuned for the latter, so the inline
  // variant needs 4px more to bleed to the same paper edge (editorShellStyles).
  el.className = 'mh-page-seam mh-page-seam-inline';
  el.style.paddingTop = `${entry.filler}px`;
  el.setAttribute('aria-hidden', 'true');
  el.setAttribute('contenteditable', 'false');
  el.setAttribute('data-testid', 'page-seam');

  const rule = document.createElement('div');
  rule.className = 'mh-page-seam-rule';
  const num = document.createElement('span');
  num.className = 'mh-page-seam-num';
  // Page that ENDS here (bare integer, no trailing period).
  num.textContent = `${entry.page}`;
  rule.appendChild(num);
  el.appendChild(rule);

  return el;
}

/** One widget decoration per doc child whose `id` attr is a key in `map`,
 *  positioned at the child's own start (the boundary immediately before it
 *  opens) — `side: -1` keeps the widget anchored to "before this node" when
 *  content is inserted right at that boundary (e.g. typing at the very start
 *  of the seamed element must not slip the widget after the new text). */
export function buildPageSeamDecorations(doc: PMNode, map: PageSeamMap): DecorationSet {
  if (map.size === 0) return DecorationSet.empty;
  const decorations: Decoration[] = [];
  let offset = 0;
  doc.forEach((node) => {
    const entry = map.get(node.attrs.id as string);
    if (entry) {
      decorations.push(
        Decoration.widget(offset, () => seamDom(entry), {
          side: -1,
          key: `seam-${node.attrs.id as string}`,
        }),
      );
    }
    offset += node.nodeSize;
  });
  return DecorationSet.create(doc, decorations);
}

export interface PageSeamsRef {
  current: PageSeamMap;
}

/** `Extension.create` wrapper so `TipTapSceneEditor`'s extensions array can
 *  include this alongside the other user extensions without reaching for the
 *  raw `Plugin` API at the call site. */
export function createPageSeamExtension(pageSeamsRef: PageSeamsRef) {
  return Extension.create({
    name: 'pageSeamDecorations',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: pageSeamPluginKey,
          props: {
            decorations(state: EditorState) {
              return buildPageSeamDecorations(state.doc, pageSeamsRef.current);
            },
          },
        }),
      ];
    },
  });
}
