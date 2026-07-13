/**
 * TipTap/ProseMirror node schema for the M0 spike (script-editor TipTap
 * migration epic, decision D2 —
 * docs/superpowers/specs/2026-07-13-script-editor-tiptap-migration.md).
 *
 * `doc = scriptElement+` — every top-level node is a `scriptElement` carrying
 * the ScriptElement's identity (`id`) and type (`elType`) as PM attrs, with
 * plain-text content only (no marks in M0; the eventual Mention inline node
 * is D6/M2 scope). `docModel.ts` converts between this schema's JSON shape
 * and `ScriptElement[]`; `opsMapper.ts` diffs two element lists into an
 * anchored `ElementOp[]` batch.
 *
 * This file is PURE SCHEMA — no NodeView. The interactive row DOM (gutter +
 * drag handle + tick) lives in `TipTapSceneEditor.tsx`, which `.extend()`s
 * `ScriptElementNode` with `addNodeView`. Keeping the two separate lets
 * docModel's round-trip tests exercise the schema without mounting React.
 */
import { Node, mergeAttributes } from '@tiptap/core';
import Document from '@tiptap/extension-document';
import Text from '@tiptap/extension-text';
import { isElementType, type ElementType } from '../types';

export const SCRIPT_ELEMENT_NODE_NAME = 'scriptElement';

/**
 * A `scriptElement` node's PM attrs. `characterId` defaults to `null` (never
 * `undefined`) — PM attrs are always fully populated with a concrete value,
 * so `docToElements` treats `null` as "no character" uniformly. See
 * docModel.ts's module doc for the full convention this implies.
 */
export interface ScriptElementAttrs {
  id: string;
  elType: ElementType;
  characterId: string | null;
}

/** `doc = scriptElement+` (D2) — overrides Document's default `block+`. */
export const ScriptDocument = Document.extend({
  content: `${SCRIPT_ELEMENT_NODE_NAME}+`,
});

/** Re-exported so callers assembling the extensions array don't need a
 *  second import path for the plain-text inline node `scriptElement`'s
 *  `text*` content requires. */
export { Text as ScriptText };

/** One screenplay row. `content: 'text*'` — plain text only, no marks in M0. */
export const ScriptElementNode = Node.create({
  name: SCRIPT_ELEMENT_NODE_NAME,
  group: 'block',
  content: 'text*',

  addAttributes() {
    return {
      id: {
        default: null,
        parseHTML: (element: HTMLElement) => element.getAttribute('data-el-id'),
        renderHTML: (attributes: ScriptElementAttrs) =>
          attributes.id ? { 'data-el-id': attributes.id } : {},
      },
      elType: {
        default: 'action' satisfies ElementType,
        parseHTML: (element: HTMLElement): ElementType => {
          const raw = element.getAttribute('data-el-type');
          return isElementType(raw) ? raw : 'action';
        },
        renderHTML: (attributes: ScriptElementAttrs) => ({ 'data-el-type': attributes.elType }),
      },
      characterId: {
        default: null,
        parseHTML: (element: HTMLElement) => element.getAttribute('data-character-id') || null,
        renderHTML: (attributes: ScriptElementAttrs) =>
          attributes.characterId ? { 'data-character-id': attributes.characterId } : {},
      },
    };
  },

  parseHTML() {
    return [{ tag: 'div[data-el-id]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes), 0];
  },
});
