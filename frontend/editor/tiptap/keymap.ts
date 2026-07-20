/**
 * TipTap keyboard extension implementing the screenplay editing semantics
 * (spec D4 / M1). Consumes `TAB_TYPE` / `SHIFT_TAB_TYPE` / `ENTER_NEW_TYPE`
 * from `../machineTables` — the SAME tables the legacy `editorMachine`
 * consumes — so type-cycling stays in lockstep by construction. This is the
 * FIRST thing M1 wires in: M0 left default ProseMirror Enter/Backspace
 * active, and default `splitBlock` copies the split node's attrs verbatim,
 * producing two `scriptElement` nodes sharing the same `id` — a real bug.
 *
 * Registering this extension in `TipTapSceneEditor`'s extensions array is
 * enough to PRE-EMPT `@tiptap/core`'s built-in `Keymap` core extension for
 * the same bindings: tiptap builds one PM `keymap()` plugin per extension,
 * and `ExtensionManager.plugins` reverses the (already priority-sorted)
 * extension list before re-sorting by (stable) priority — so among
 * equal-priority extensions, USER extensions' plugins land BEFORE core
 * extensions' plugins in the final `state.plugins` array. ProseMirror tries
 * each plugin's `handleKeyDown` in that array order and stops at the first
 * `true`, so as long as every handler below returns `true` (or `false` only
 * to intentionally defer to the browser — see IME below), the core Keymap's
 * `splitBlock`/`joinBackward`/etc. never run.
 *
 * Unlike the legacy per-row contentEditable model (Enter/Tab/Backspace
 * always act on the WHOLE row, ignoring caret position — the machine has no
 * concept of a text offset), a real ProseMirror node supports genuine
 * mid-text splitting, so Enter here ALSO splits text at the caret (head
 * stays in the current node, tail moves to a fresh node) instead of
 * legacy's always-empty-new-line approximation. This is the M1 task's
 * explicit "parity-plus" instruction, not a divergence bug — the TYPE
 * assignment (which new type gets created, Tab/Shift-Tab retyping) is what
 * must match the legacy tables; the DOM-splitting mechanics are naturally
 * richer because PM tracks a real caret offset.
 *
 * IME: every handler bails (`return false`) when `editor.view.composing` is
 * true, so a Chinese/Japanese/Korean composition session is never
 * interrupted mid-keystroke by our machine.
 */
import { Extension, type Editor } from '@tiptap/core';
import { TextSelection } from '@tiptap/pm/state';
import type { Node as PMNode, ResolvedPos } from '@tiptap/pm/model';
import { TAB_TYPE, SHIFT_TAB_TYPE, ENTER_NEW_TYPE } from '../machineTables';
import { newElementId } from '../sceneService';
import { SCRIPT_ELEMENT_NODE_NAME } from './schema';
import type { ElementType } from '../types';

/** The scriptElement node + position context the caret currently sits in. */
export interface ElementCtx {
  /** Absolute position immediately BEFORE the node opens. */
  pos: number;
  node: PMNode;
  /** Sibling index within the doc (0-based). */
  index: number;
  /** Character offset within the node's text content (0 = node start). */
  localOffset: number;
}

/**
 * Resolve the scriptElement node (if any) the given position sits inside.
 * Exported so `TipTapSceneEditor` can reuse the exact same resolution logic
 * for its selection-tracking (`onFocusCursor` / caret-preserving external
 * apply) instead of re-deriving it.
 */
export function getElementCtx($from: ResolvedPos): ElementCtx | null {
  const depth = $from.depth;
  if (depth < 1 || $from.parent.type.name !== SCRIPT_ELEMENT_NODE_NAME) return null;
  const pos = $from.before(depth);
  const node = $from.parent;
  const start = $from.start(depth);
  const index = $from.index(depth - 1);
  const localOffset = $from.pos - start;
  return { pos, node, index, localOffset };
}

/** Absolute start position of the doc's `index`-th top-level child. */
function childPos(doc: PMNode, index: number): number {
  let p = 0;
  for (let i = 0; i < index; i += 1) p += doc.child(i).nodeSize;
  return p;
}

/** Retype the node at `pos` in place (attrs-only change; caret unaffected). */
function retype(editor: Editor, pos: number, node: PMNode, newType: ElementType): boolean {
  const { state, view } = editor;
  let tr = state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, elType: newType });
  // A Tab/Shift-Tab retype keeps the caret where it was, so onSelectionUpdate
  // never runs and the empty-cue picker (the only affordance an empty character
  // line offers) would not open. Flag the transaction so the editor's onUpdate
  // can invite it.
  if (newType === 'character' && node.textContent.trim() === '') {
    tr = tr.setMeta('emptyCueRetype', node.attrs.id as string);
  }
  view.dispatch(tr);
  return true;
}

/** Move the caret to an absolute position with no other change. */
function moveCaretTo(editor: Editor, pos: number): boolean {
  const { state, view } = editor;
  const tr = state.tr.setSelection(TextSelection.create(state.doc, pos));
  view.dispatch(tr);
  return true;
}

/**
 * Enter: split/insert per the current node's `elType` (ENTER_NEW_TYPE), with
 * the paren special case (step into a following dialogue instead of
 * inserting, when one exists). An active (non-collapsed) selection is
 * deleted first so the split always starts from a collapsed caret. The new
 * node always gets a fresh id; a mid-text Enter splits the text (head stays,
 * tail moves to the new node); caret lands at the new/target node's start.
 */
function handleEnter(editor: Editor): boolean {
  if (editor.view.composing) return false;
  const { state, view } = editor;
  let tr = state.tr;
  if (!state.selection.empty) {
    tr = tr.delete(state.selection.from, state.selection.to);
  }
  const ctx = getElementCtx(tr.selection.$from);
  if (!ctx) return false;
  const { pos, node, index, localOffset } = ctx;
  const elType = node.attrs.elType as ElementType;
  const text = node.textContent;
  const tailText = text.slice(localOffset);
  const stepsBeforeSplit = tr.steps.length;

  const nextNode = index + 1 < tr.doc.childCount ? tr.doc.child(index + 1) : null;
  if (elType === 'paren' && nextNode && (nextNode.attrs.elType as ElementType) === 'dialogue') {
    // Step into the following dialogue — no ops, no split.
    const nextStart = pos + node.nodeSize + 1;
    view.dispatch(tr.setSelection(TextSelection.create(tr.doc, nextStart)));
    return true;
  }
  const newType: ElementType = elType === 'paren' ? 'dialogue' : ENTER_NEW_TYPE[elType];

  // Remove the tail text from the current node — it becomes the new node's
  // content. A caret at the end (tailText === '') is a true no-op delete.
  const deleteFrom = pos + 1 + localOffset;
  const deleteTo = pos + 1 + text.length;
  if (deleteTo > deleteFrom) tr = tr.delete(deleteFrom, deleteTo);

  const newId = newElementId();
  const schema = tr.doc.type.schema;
  const nodeType = schema.nodes[SCRIPT_ELEMENT_NODE_NAME];
  const content = tailText.length > 0 ? schema.text(tailText) : null;
  const newNode = nodeType.create({ id: newId, elType: newType, characterId: null }, content);

  // `pos + node.nodeSize` (the end of the CURRENT node) was captured against
  // the doc snapshot right after ctx resolution — map it through only the
  // steps applied SINCE then (the tail-text delete above), not the whole
  // transaction's mapping (which would double-apply any selection-delete
  // step already folded into `pos` itself).
  const insertPos = tr.mapping.slice(stepsBeforeSplit).map(pos + node.nodeSize);
  tr = tr.insert(insertPos, newNode);
  const cursorPos = insertPos + 1;
  tr = tr.setSelection(TextSelection.create(tr.doc, cursorPos));
  tr.scrollIntoView();
  view.dispatch(tr);
  return true;
}

/**
 * Backspace at node start: an EMPTY node is deleted and the caret moves to
 * the previous node's end (or the new first node's start, if there is no
 * previous node — i.e. the empty node itself was first); a NON-EMPTY node
 * at offset 0 is a no-op (legacy never joins lines on Backspace). The sole
 * remaining node in the doc is never deleted (the PM schema requires
 * `scriptElement+` — at least one). Any offset other than 0, or a
 * non-collapsed selection, defers to the browser's default character
 * delete.
 */
function handleBackspace(editor: Editor): boolean {
  if (editor.view.composing) return false;
  const { state, view } = editor;
  if (!state.selection.empty) return false;
  const ctx = getElementCtx(state.selection.$from);
  if (!ctx) return false;
  if (ctx.localOffset !== 0) return false;

  const { pos, node, index } = ctx;
  if (node.textContent !== '') return true; // non-empty at start: no-op, never join lines
  if (state.doc.childCount <= 1) return true; // sole remaining node: keep it

  let tr = state.tr.delete(pos, pos + node.nodeSize);
  const cursorPos = index > 0 ? pos - 1 : 1;
  tr = tr.setSelection(TextSelection.create(tr.doc, cursorPos));
  tr.scrollIntoView();
  view.dispatch(tr);
  return true;
}

/** Tab: retype the current node per TAB_TYPE. Always handled. */
function handleTab(editor: Editor): boolean {
  if (editor.view.composing) return false;
  const { state } = editor;
  const ctx = getElementCtx(state.selection.$from);
  if (!ctx) return true; // preventDefault regardless — never leave the editor
  const { pos, node } = ctx;
  const elType = node.attrs.elType as ElementType;
  return retype(editor, pos, node, TAB_TYPE[elType]);
}

/**
 * Shift-Tab: retype the current node per SHIFT_TAB_TYPE, with the dialogue
 * special case — step back to a preceding character cue (caret to its end)
 * if one exists in the doc, else fall back to retyping this line as a
 * character. Always handled.
 */
function handleShiftTab(editor: Editor): boolean {
  if (editor.view.composing) return false;
  const { state } = editor;
  const ctx = getElementCtx(state.selection.$from);
  if (!ctx) return true; // preventDefault regardless — never leave the editor
  const { pos, node, index } = ctx;
  const elType = node.attrs.elType as ElementType;

  if (elType === 'dialogue') {
    for (let i = index - 1; i >= 0; i -= 1) {
      const sibling = state.doc.child(i);
      if ((sibling.attrs.elType as ElementType) === 'character') {
        const siblingStart = childPos(state.doc, i);
        const siblingEnd = siblingStart + sibling.nodeSize - 1;
        return moveCaretTo(editor, siblingEnd);
      }
    }
    return retype(editor, pos, node, 'character');
  }

  return retype(editor, pos, node, SHIFT_TAB_TYPE[elType]);
}

export const ScriptKeymap = Extension.create({
  name: 'scriptKeymap',
  addKeyboardShortcuts() {
    return {
      Enter: () => handleEnter(this.editor),
      Tab: () => handleTab(this.editor),
      'Shift-Tab': () => handleShiftTab(this.editor),
      Backspace: () => handleBackspace(this.editor),
      // Esc leaves the editing surface (legacy parity): blur the view so the
      // shell's onBlur → onExitEditing relaxes the data-editing toolbar hook.
      Escape: () => {
        this.editor.commands.blur();
        return true;
      },
    };
  },
});
