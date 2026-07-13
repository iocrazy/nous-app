/**
 * A TipTap editor over one scene's `ScriptElement[]`, rendering today's row
 * DOM (spec D5) via a NodeView, with the M1 sync pipeline wired in (spec
 * phases): the ported keyboard machine (`./keymap`), a structural-vs-text
 * dispatch split (structural ops dispatch immediately; text-only updates
 * debounce 500ms, matching `SceneBlock`'s legacy `INPUT_DEBOUNCE_MS`),
 * remote/external apply via an imperative ref with a transaction-meta loop
 * guard, and IME-safe composition handling.
 *
 * `initialElements` is read ONCE (mount snapshot) — this component does not
 * react to that prop changing after mount (M0's known limitation); the
 * caller (`SceneBlock`) MUST route any later remote/conflict/reconcile
 * update through `applyExternalElements` (exposed via ref) instead. This
 * split is deliberate: an ordinary re-render (e.g. a parent state churn
 * unrelated to this scene) must never re-seed the doc and blow away
 * in-progress local edits/caret position — only an EXPLICIT external-apply
 * call does that, and even then only when the incoming elements actually
 * differ from what's on screen.
 */
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from 'react';
import {
  EditorContent,
  NodeViewContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  useEditor,
  type NodeViewProps,
} from '@tiptap/react';
import { Node as PMNode } from '@tiptap/pm/model';
import { TextSelection } from '@tiptap/pm/state';
import { ScriptDocument, ScriptElementNode, ScriptText, type ScriptElementAttrs } from './schema';
import { docToElements, elementsToDoc } from './docModel';
import { mapDocChange } from './opsMapper';
import { ScriptKeymap, getElementCtx } from './keymap';
import { applyLocal } from '../opBuilder';
import type { ElementOp, ScriptElement } from '../types';
import { HOLLYWOOD_LINE_CLASS } from '../render/HollywoodLayout';
import { ASIAN_LINE_CLASS } from '../render/AsianLayout';

export type SceneFormat = 'hollywood' | 'asian';

/** Text-edit dispatch debounce — matches `SceneBlock`'s `INPUT_DEBOUNCE_MS`. */
const INPUT_DEBOUNCE_MS = 500;

export interface TipTapSceneEditorProps {
  /** Mount-time snapshot only — see module doc. */
  initialElements: ScriptElement[];
  format: SceneFormat;
  /** A1 continuous numbering base (see HollywoodLayout/AsianLayout's prop of
   *  the same name) — the heading row consumes `blockIndexBase + 1`, so the
   *  first element here displays as `blockIndexBase + 2`. Defaults to 0. */
  blockIndexBase?: number;
  /** Every op batch this editor produces routes through here — the SAME
   *  `useSceneSync.dispatchOps(ops, optimistic)` contract SceneBlock's
   *  legacy path uses. Structural ops (insert/delete/move, or a retype)
   *  call this immediately; pure text edits debounce 500ms first. */
  dispatchOps: (ops: ElementOp[], optimistic: ScriptElement[]) => void;
  /** Reports the currently-focused element's id (or null) on every selection
   *  change, so the shell/toolbar can follow the cursor. */
  onFocusCursor?: (elementId: string | null) => void;
}

export interface TipTapSceneEditorHandle {
  /**
   * Apply a remote/reconciled/conflict-resolved elements list. No-ops when
   * `elements` is already what the doc shows (field-wise equality, not
   * reference equality) — the loop guard that keeps a remote echo of our
   * OWN edit from re-triggering itself. Otherwise rebuilds the doc content
   * in one transaction tagged `externalSync` (skipped by `onUpdate`, so it
   * never re-emits ops) and best-effort restores the caret into the
   * previously-focused element (clamped to its new text length).
   */
  applyExternalElements: (elements: ScriptElement[]) => void;
}

/** Field-wise equality — mirrors `useSceneSync`'s `sameElements` so a
 *  server-normalized round-trip (e.g. `character_id` explicit-null) never
 *  reads as a spurious diff. */
function elementsEqual(a: ScriptElement[], b: ScriptElement[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((el, i) => {
    const other = b[i];
    return (
      el.id === other.id &&
      el.type === other.type &&
      el.text === other.text &&
      (el.character_id ?? null) === (other.character_id ?? null)
    );
  });
}

/** True when `op` should dispatch immediately rather than debounce: any
 *  insert/delete/move, or an `update` whose payload touches `type` (a
 *  retype changes numbering/toolbar state, which must never lag). A pure
 *  text (and/or character_id-only) update debounces. */
function isStructuralOp(op: ElementOp): boolean {
  if (op.op !== 'update') return true;
  return op.payload != null && 'type' in op.payload;
}

/** The absolute start position + node of the doc's child carrying `id`. */
function findNodeById(doc: PMNode, id: string): { pos: number; node: PMNode } | null {
  let result: { pos: number; node: PMNode } | null = null;
  let offset = 0;
  doc.forEach((node) => {
    if (!result && node.attrs.id === id) result = { pos: offset, node };
    offset += node.nodeSize;
  });
  return result;
}

interface ScriptElementViewRefs {
  formatRef: MutableRefObject<SceneFormat>;
  blockIndexBaseRef: MutableRefObject<number>;
}

/** The row DOM (spec D5): gutter [num + 4-dot drag handle] + tick + content. */
function ScriptElementView({ node, editor, getPos }: NodeViewProps, refs: ScriptElementViewRefs) {
  const attrs = node.attrs as ScriptElementAttrs;

  // No reliable static hook into "this node's sibling index changed" — the
  // safe, simple-for-M0 approach (per spec: "keep simple") is to re-render
  // on every editor transaction and recompute from getPos() fresh.
  const [, bumpTick] = useState(0);
  useEffect(() => {
    const rerender = () => bumpTick((t) => t + 1);
    editor.on('transaction', rerender);
    return () => {
      editor.off('transaction', rerender);
    };
  }, [editor]);

  let siblingIndex = 0;
  if (typeof getPos === 'function') {
    const pos = getPos();
    if (typeof pos === 'number') {
      siblingIndex = editor.state.doc.resolve(pos).index(0);
    }
  }
  const displayIndex = refs.blockIndexBaseRef.current + 2 + siblingIndex;

  const lineClass =
    refs.formatRef.current === 'asian'
      ? ASIAN_LINE_CLASS[attrs.elType]
      : HOLLYWOOD_LINE_CLASS[attrs.elType];

  return (
    <NodeViewWrapper as="div" className="mh-el-row">
      <div className="mh-el-gutter" contentEditable={false}>
        <span className="mh-el-num">{displayIndex}</span>
        <button type="button" className="mh-el-drag" contentEditable={false} tabIndex={-1}>
          <span className="mh-el-dot" />
          <span className="mh-el-dot" />
          <span className="mh-el-dot" />
          <span className="mh-el-dot" />
        </button>
      </div>
      <span className={`mh-el-tick t-${attrs.elType}`} contentEditable={false} aria-hidden="true" />
      <NodeViewContent
        as="div"
        className={`mh-el-editable mh-el-line ${lineClass}`}
        data-el-type={attrs.elType}
        data-el-id={attrs.id}
      />
    </NodeViewWrapper>
  );
}

export const TipTapSceneEditor = forwardRef<TipTapSceneEditorHandle, TipTapSceneEditorProps>(
  function TipTapSceneEditor(
    { initialElements, format, blockIndexBase = 0, dispatchOps, onFocusCursor },
    ref,
  ) {
    // Latest-callback / latest-value refs — read fresh inside the NodeView and
    // onUpdate closures captured once at editor creation (NoteEditor.tsx's
    // established pattern in this repo).
    const dispatchOpsRef = useRef(dispatchOps);
    dispatchOpsRef.current = dispatchOps;
    const onFocusCursorRef = useRef(onFocusCursor);
    onFocusCursorRef.current = onFocusCursor;
    const formatRef = useRef<SceneFormat>(format);
    formatRef.current = format;
    const blockIndexBaseRef = useRef(blockIndexBase);
    blockIndexBaseRef.current = blockIndexBase;

    // The mapper's diff base: the last element list dispatched to (or
    // adopted from, via applyExternalElements) the server. Advances on every
    // dispatch — NOT on every keystroke, which is what makes the text debounce
    // work (see onUpdate below).
    const lastEmittedRef = useRef<ScriptElement[]>(initialElements);
    // The latest doc snapshot behind an UNFLUSHED text-only debounce, or null
    // when nothing is pending. Read by the debounce timer, blur, unmount, and
    // the post-composition flush.
    const pendingNextRef = useRef<ScriptElement[] | null>(null);
    const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // The currently-focused element's id + in-node text offset, tracked off
    // selection changes — used to best-effort restore the caret across an
    // applyExternalElements rebuild.
    const focusedElementIdRef = useRef<string | null>(null);
    const focusedOffsetRef = useRef(0);

    const initialContent = useMemo(() => elementsToDoc(initialElements), []); // eslint-disable-line react-hooks/exhaustive-deps

    const extensions = useMemo(() => {
      const refs: ScriptElementViewRefs = { formatRef, blockIndexBaseRef };
      const ViewNode = ScriptElementNode.extend({
        addNodeView() {
          return ReactNodeViewRenderer((props: NodeViewProps) => ScriptElementView(props, refs));
        },
      });
      return [ScriptDocument, ScriptText, ViewNode, ScriptKeymap];
    }, []);

    const clearDebounce = useCallback(() => {
      if (debounceTimerRef.current !== null) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
    }, []);

    /** Dispatch whatever text-only diff is pending right now (debounce fire,
     *  blur, unmount, or the post-composition catch-up). No-op if clean. */
    const flushPending = useCallback(() => {
      debounceTimerRef.current = null;
      const pendingNext = pendingNextRef.current;
      pendingNextRef.current = null;
      if (pendingNext === null) return;
      const ops = mapDocChange(lastEmittedRef.current, pendingNext);
      if (ops.length === 0) return;
      const optimistic = applyLocal(lastEmittedRef.current, ops);
      dispatchOpsRef.current(ops, optimistic);
      lastEmittedRef.current = optimistic;
    }, []);

    const editor = useEditor({
      extensions,
      content: initialContent,
      editable: true,
      editorProps: {
        attributes: { class: 'mh-tiptap-scene-editor' },
      },
      onUpdate: ({ editor: ed, transaction }) => {
        // Our own applyExternalElements-produced transaction — never re-emit.
        if (transaction.getMeta('externalSync')) return;

        const next = docToElements(ed.state.doc);

        // Never dispatch mid-composition (Chinese/Japanese/Korean IME) — just
        // remember the latest snapshot; the compositionend listener below
        // (or the next post-composition onUpdate) drives the actual flush.
        if (ed.view.composing) {
          pendingNextRef.current = next;
          return;
        }

        const ops = mapDocChange(lastEmittedRef.current, next);
        if (ops.length === 0) {
          pendingNextRef.current = null;
          return;
        }

        if (ops.some(isStructuralOp)) {
          // Structural: dispatch NOW. This batch is computed from
          // `lastEmittedRef` (unmoved since the last real dispatch), so it
          // automatically folds in any still-pending text edit too — no
          // separate "flush pending first" step needed.
          clearDebounce();
          pendingNextRef.current = null;
          const optimistic = applyLocal(lastEmittedRef.current, ops);
          dispatchOpsRef.current(ops, optimistic);
          lastEmittedRef.current = optimistic;
        } else {
          // Text-only: remember the latest snapshot and (re)start the debounce.
          pendingNextRef.current = next;
          clearDebounce();
          debounceTimerRef.current = setTimeout(flushPending, INPUT_DEBOUNCE_MS);
        }
      },
      onSelectionUpdate: ({ editor: ed }) => {
        const ctx = getElementCtx(ed.state.selection.$from);
        const id = ctx ? (ctx.node.attrs.id as string) : null;
        focusedElementIdRef.current = id;
        focusedOffsetRef.current = ctx ? ctx.localOffset : 0;
        onFocusCursorRef.current?.(id);
      },
      onBlur: () => {
        clearDebounce();
        flushPending();
      },
    });

    // Flush on unmount too (view-switch guard, mirrors SceneBlock's own
    // unmount-flush for its input debounce).
    useEffect(() => () => flushPending(), [flushPending]);

    // Composition end: PM defers doc sync while `view.composing` is true, so
    // the "final" onUpdate carrying the composed text may land in the SAME
    // tick as the native compositionend event, or a microtask later — defer
    // the catch-up flush check to the next tick so it always sees it.
    useEffect(() => {
      if (!editor) return undefined;
      const dom = editor.view.dom;
      const onCompositionEnd = () => {
        setTimeout(() => {
          if (pendingNextRef.current !== null) flushPending();
        }, 0);
      };
      dom.addEventListener('compositionend', onCompositionEnd);
      return () => dom.removeEventListener('compositionend', onCompositionEnd);
    }, [editor, flushPending]);

    const applyExternalElements = useCallback(
      (elements: ScriptElement[]) => {
        if (!editor || elements.length === 0) return;
        const current = docToElements(editor.state.doc);
        if (elementsEqual(current, elements)) return;

        clearDebounce();
        pendingNextRef.current = null;

        const parsed = PMNode.fromJSON(editor.state.schema, elementsToDoc(elements));
        let tr = editor.state.tr.replaceWith(0, editor.state.doc.content.size, parsed.content);
        tr.setMeta('externalSync', true);

        const focusedId = focusedElementIdRef.current;
        if (focusedId) {
          const target = findNodeById(tr.doc, focusedId);
          if (target) {
            const clamped = Math.min(focusedOffsetRef.current, target.node.textContent.length);
            tr = tr.setSelection(TextSelection.create(tr.doc, target.pos + 1 + clamped));
          }
        }

        editor.view.dispatch(tr);
        lastEmittedRef.current = elements;
      },
      [editor, clearDebounce],
    );

    useImperativeHandle(ref, () => ({ applyExternalElements }), [applyExternalElements]);

    useEffect(() => {
      if (import.meta.env.MODE !== 'test') return;
      // Test-only backdoor (mirrors NoteEditor.tsx) — jsdom can't drive real
      // keyboard/IME input into ProseMirror, so tests reach the editor
      // instance directly to exercise commands.
      (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance = editor;
    }, [editor]);

    return (
      <div className="mh-tiptap-scene-editor-root">
        <EditorContent editor={editor} />
      </div>
    );
  },
);
