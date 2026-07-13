/**
 * Mention CHIPS as inline ProseMirror decorations (spec D5/M3 item 5,
 * M2's carry-over — M2 shipped `@Name` as plain text only, no chip markup).
 *
 * `layoutShared.ts`'s `scanMentionRuns` is the single source of the
 * greedy-longest-candidate match rules; this plugin re-runs it over each
 * `scriptElement` node's own text and wraps the matched `[start, end)`
 * character range in an INLINE decoration carrying the identical
 * `mh-mention`/`mh-mention unknown` class + `data-mention` attribute
 * `buildElementHtml` (the legacy contentEditable HTML-string renderer)
 * produces — same visual chip, same "known vs grey fallback" rule, but never
 * mutating the underlying text (a real PM inline decoration, not an
 * innerHTML rewrite), so typing inside/around a chip is ordinary PM text
 * editing with no caret-ownership hazard.
 *
 * Like `pageSeamPlugin.ts`, this holds no state: `decorations(state)` walks
 * `state.doc` fresh on every call using the live `mentionCandidatesRef`. PM
 * calls this after every transaction, so typing (which is *always* a doc
 * change) re-derives chip ranges for free; a candidates-list prop change with
 * no doc edit is forced through by the same `propsSync` no-op transaction
 * `TipTapSceneEditor` already dispatches for `pageSeams`/`selectedElementIds`
 * (see its module doc) — `mentionCandidates` was added to that effect's
 * dependency array too.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import type { Node as PMNode } from '@tiptap/pm/model';
import type { EditorState } from '@tiptap/pm/state';
import { scanMentionRuns } from '../render/layoutShared';

export const mentionDecorationPluginKey = new PluginKey<null>('mentionDecorations');

export interface MentionCandidatesRef {
  current: string[];
}

/** One inline decoration per `@name` run found in `doc`'s children — mirrors
 *  `buildElementHtml`'s per-character escaping concerns not at all (no HTML
 *  string involved): PM decorations reference existing text by RANGE, so
 *  there is nothing to escape or mutate. */
export function buildMentionDecorations(doc: PMNode, candidates: string[]): DecorationSet {
  const decorations: Decoration[] = [];
  let offset = 0;
  doc.forEach((node) => {
    const text = node.textContent;
    if (text.length > 0 && text.includes('@')) {
      const runs = scanMentionRuns(text, candidates);
      for (const run of runs) {
        decorations.push(
          Decoration.inline(offset + 1 + run.start, offset + 1 + run.end, {
            class: `mh-mention${run.isKnown ? '' : ' unknown'}`,
            'data-mention': run.name,
          }),
        );
      }
    }
    offset += node.nodeSize;
  });
  return decorations.length > 0 ? DecorationSet.create(doc, decorations) : DecorationSet.empty;
}

export function createMentionDecorationExtension(candidatesRef: MentionCandidatesRef) {
  return Extension.create({
    name: 'mentionDecorations',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: mentionDecorationPluginKey,
          props: {
            decorations(state: EditorState) {
              return buildMentionDecorations(state.doc, candidatesRef.current);
            },
          },
        }),
      ];
    },
  });
}
