/**
 * Pure converters between `ScriptElement[]` (the app's editable model — spec
 * D3) and the TipTap/ProseMirror doc shape defined in `schema.ts`. No React,
 * no editor instance — these run against `JSONContent` literals in tests and
 * against the live `editor.state.doc` at runtime.
 *
 * CONVENTION — `character_id` is always explicit here, never omitted: every
 * ScriptElement this module PRODUCES carries `character_id` as a string OR
 * `null`, never an absent key. This matches PM: a node attr always resolves
 * to a concrete value (its declared default is `null`), so "the key was
 * never set" and "the key was explicitly cleared" collapse to the same wire
 * shape the instant data passes through a real PM node — there is no way to
 * recover that distinction on the way back out. Fixtures that round-trip
 * through this module (docModel tests, opsMapper tests, TipTapSceneEditor)
 * should follow the same convention (always pass `character_id: null` rather
 * than omitting the key) so `docToElements(elementsToDoc(els))` stays a
 * byte-for-byte match.
 *
 * Round-trip law (pinned by golden tests in __tests__/tiptapM0.test.ts):
 *   docToElements(elementsToDoc(els)) deep-equals els
 * for any `els` that follows the convention above.
 */
import type { JSONContent } from '@tiptap/core';
import type { Node as ProseMirrorNode } from '@tiptap/pm/model';
import { isElementType, type ScriptElement } from '../types';
import { SCRIPT_ELEMENT_NODE_NAME } from './schema';

/** `ScriptElement[]` → the TipTap `JSONContent` doc (feeds `useEditor({ content })`). */
export function elementsToDoc(elements: ScriptElement[]): JSONContent {
  return {
    type: 'doc',
    content: elements.map((el) => ({
      type: SCRIPT_ELEMENT_NODE_NAME,
      attrs: {
        id: el.id,
        elType: el.type,
        characterId: el.character_id ?? null,
      },
      // PM disallows an empty text node — omit `content` entirely rather
      // than emitting `content: [{ type: 'text', text: '' }]`.
      ...(el.text.length > 0 ? { content: [{ type: 'text' as const, text: el.text }] } : {}),
    })),
  };
}

/** True when `doc` is a live ProseMirror Node (has `.toJSON`), not a plain JSONContent literal. */
function isProseMirrorNode(doc: ProseMirrorNode | JSONContent): doc is ProseMirrorNode {
  return typeof (doc as ProseMirrorNode).toJSON === 'function';
}

/** The TipTap doc (live PM node, or a JSONContent literal) → `ScriptElement[]`. */
export function docToElements(doc: ProseMirrorNode | JSONContent): ScriptElement[] {
  const json: JSONContent = isProseMirrorNode(doc) ? doc.toJSON() : doc;
  const nodes = json.content ?? [];
  return nodes.map((node) => {
    const attrs = (node.attrs ?? {}) as {
      id?: string;
      elType?: string;
      characterId?: string | null;
    };
    const text = (node.content ?? [])
      .filter((child) => child.type === 'text')
      .map((child) => child.text ?? '')
      .join('');
    return {
      id: attrs.id ?? '',
      type: isElementType(attrs.elType) ? attrs.elType : 'action',
      text,
      character_id: attrs.characterId ?? null,
    };
  });
}
