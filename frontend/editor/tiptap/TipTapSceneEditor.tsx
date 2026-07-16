/**
 * A TipTap editor over one scene's `ScriptElement[]`, rendering today's row
 * DOM (spec D5) via a NodeView, with the M1 sync pipeline (keymap/debounce/
 * external-apply/IME) and the M2 block-UX port (spec D6 — gutter tick, same
 * + cross-scene drag, slash menu, mentions/character-cue, TypeCommand)
 * wired in.
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
 *
 * ── M3 — format switch recreates the editor (deliberate choice) ───────────
 * `format` (hollywood/asian) is ALSO read once at mount, same as
 * `initialElements`, for a structural reason: the Asian row DOM nests an
 * extra `.as-row` wrapper + optional `△`/`：` ornament spans AROUND the
 * `.mh-el-row` the Hollywood engine renders bare (spec D5 — see
 * `ScriptElementView` below), and `ReactNodeViewRenderer` re-renders the
 * SAME NodeView component tree, gated by the perf-sensitive
 * childCount/propsSync tick in `ScriptElementView` (M2 item 1c) — routing a
 * live format flip through that path would mean either bumping the tick on
 * every format change too (defeating the perf gate's whole purpose, since
 * format changes are rare but would need to force-repaint every row anyway)
 * or accepting a stale DOM shape until the next incidental transaction. A
 * format toggle is a deliberate, infrequent user action (a script-wide
 * setting), not a per-keystroke hot path, so `SceneBlock` keys the
 * `<TipTapSceneEditor key={format}>` element on format instead — React fully
 * unmounts the old editor (flushing any pending debounce via the unmount
 * effect below, so no edit is lost) and mounts a fresh one from the CURRENT
 * `sync.elements`. The one user-visible cost is caret position resets on a
 * format switch — an acceptable trade for a structural DOM change that is
 * not a per-keystroke event.
 *
 * ── M2 architecture note — drag/drop and toolbar-retype bypass the mapper ──
 * Same-scene reorder, cross-scene insert, and TypeCommand retype are all
 * ALREADY fully implemented in `SceneBlock` for the legacy layout engines
 * (`handleElementDragStart/Over/Drop/DragEnd`, `acceptExternalDrop`, the
 * `typeCommand` effect) — none of that logic references the layout engine
 * at all; it operates purely on `sync.elements` via `sync.dispatchOps`. So
 * rather than re-deriving those same ops through a NodeView-internal PM
 * transaction + `mapDocChange` diff (extra surface, extra risk of the mapper
 * producing a DIFFERENT op shape than the hand-built one), this NodeView
 * simply forwards drag/tick DOM events to the SAME callbacks SceneBlock
 * already passes the legacy engines. The resulting `sync.elements` change
 * flows back into the doc through the already-proven M1
 * `applyExternalElements` pipeline — one write path, one source of truth.
 * `retypeElement`/`replaceElementText` below are the one exception: a
 * dedicated small transaction gives an IMMEDIATE, caret-preserving visual
 * update (a full `applyExternalElements` doc rebuild is the fallback that
 * still runs right after and no-ops once it sees the doc already matches).
 */
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type MouseEvent as ReactMouseEvent,
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
import { TextSelection, type Transaction } from '@tiptap/pm/state';
import { ScriptDocument, ScriptElementNode, ScriptText, type ScriptElementAttrs } from './schema';
import { docToElements, elementsToDoc } from './docModel';
import { mapDocChange } from './opsMapper';
import { ScriptKeymap, getElementCtx } from './keymap';
import { createMenuBridgeKeymap, type MenuBridge } from './menuKeymap';
import { applyLocal } from '../opBuilder';
import { elementEdgeFromPointer } from '../render/layoutShared';
import type { ElementOp, ElementType, ScriptElement } from '../types';
import { HOLLYWOOD_LINE_CLASS } from '../render/HollywoodLayout';
import { ASIAN_LINE_CLASS, ASIAN_PREFIX, ASIAN_SUFFIX } from '../render/AsianLayout';
import { createPageSeamExtension, type PageSeamMap } from './pageSeamPlugin';
import { createMentionDecorationExtension } from './mentionDecorationPlugin';

export type SceneFormat = 'hollywood' | 'asian';

/**
 * Does a DOM event's target sit inside `selector`?
 *
 * A drop/dragstart landing on TEXT reports a text NODE as `event.target`, and a
 * text node has no `.closest` — the first cut of this check
 * (`(event.target as HTMLElement)?.closest?.(sel)`) therefore silently returned
 * undefined → false for exactly the common case, letting ProseMirror's built-in
 * drop run and corrupt the doc. Climb to the parent element first.
 */
function hitsSelector(target: EventTarget | null, selector: string): boolean {
  const node = target as Node | null;
  if (!node) return false;
  const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement);
  return !!el?.closest?.(selector);
}

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
  /** Fires when the editing surface loses focus (blur, or Esc which blurs it),
   *  so the shell can relax its `data-editing` toolbar emphasis. */
  onExitEditing?: () => void;
  // ── M2: copilot gutter tick ───────────────────────────────────────────
  /** Clicking a row's gutter tick selects it for the copilot (spec D6). */
  onTickClick?: (elementId: string, shiftKey: boolean) => void;
  /** Currently copilot-selected element ids (drives the tick's `.selected`). */
  selectedElementIds?: Set<string>;
  // ── M2: same/cross-scene element drag (hover-gutter handle) ──────────
  /** The element currently being dragged — from THIS scene or another one
   *  (SceneBlock arms every row once any drag is in flight; see its
   *  `elementReorder.draggingElementId` expression). */
  draggingElementId?: string | null;
  /** The current drop target row + edge (SceneBlock's `elementDropTarget`). */
  dropElementEdge?: { elementId: string; edge: 'top' | 'bottom' } | null;
  onElementDragStart?: (elementId: string) => void;
  onElementDragOver?: (elementId: string, edge: 'top' | 'bottom') => void;
  onElementDrop?: (elementId: string, edge: 'top' | 'bottom') => void;
  onElementDragEnd?: () => void;
  /** Right-click a row → open the scene context menu at the cursor. */
  onElementContextMenu?: (elementId: string, x: number, y: number) => void;
  // ── M2: slash menu ('/' at block start) ───────────────────────────────
  /** Fires on every text change with the live filter (text after `/'), or
   *  `null` when this element's line no longer starts with `/`. */
  onSlashChange?: (elementId: string, query: string | null) => void;
  /** The currently-open slash menu's keyboard-nav bridge, or null. */
  slashMenu?: MenuBridge | null;
  // ── M2: mentions + character-cue picker ───────────────────────────────
  /** Fires when an inline `@token` is being typed, or a character-cue /
   *  transition line is focused/edited — `kind` distinguishes the trigger
   *  paths (transition opens the preset picker: CUT TO: / FADE TO: / …). */
  onMentionOpen?: (
    elementId: string,
    kind: 'inline' | 'character' | 'transition',
    query: string,
  ) => void;
  /** Fires when the open mention/cue picker should close (focus left its
   *  element, or the `@` run was deleted). Safe to call when nothing is open. */
  onMentionClose?: () => void;
  /** The currently-open mention/cue picker's keyboard-nav bridge, or null. */
  mentionMenu?: MenuBridge | null;
  // ── M3: paged-mode seams + mention chips ──────────────────────────────
  /** Element-level page seams (`EditorShell`'s measurement effect), rendered
   *  as a widget decoration immediately before the matching node — see
   *  `pageSeamPlugin.ts`. Scene-level (`scene:<id>`) seams stay at the shell
   *  and never reach this component. */
  pageSeams?: PageSeamMap;
  /** The script's mention candidates (distinct CAST names) — drives the
   *  SAME `@name` chip decoration `MentionNamesContext` drives for the
   *  legacy engines (see `mentionDecorationPlugin.ts`). */
  mentionCandidates?: string[];
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
  /**
   * M2 — TypeCommand / slash-pick: retype `elementId` to `type` in a single
   * small transaction (attrs-only, or attrs + text-clear when `clearText`
   * is set, for a slash-menu pick which always empties the `/query` line
   * too). Tagged `externalSync` — the CALLER is responsible for also
   * dispatching the corresponding op via `dispatchOps`/`sync.dispatchOps`;
   * this method only drives the immediate visual update. A no-op when the
   * element id no longer exists in the doc.
   */
  retypeElement: (elementId: string, type: ElementType, clearText?: boolean) => void;
  /**
   * M2 — mention/cue selection: replace `elementId`'s entire text content
   * with `text` and land the caret at its end. Tagged `externalSync` — same
   * caller contract as `retypeElement`.
   */
  replaceElementText: (elementId: string, text: string) => void;
  /**
   * M2 — seed focus / cross-scene drop focus: move the caret into
   * `elementId` (start when `atStart`, else end) and focus the editor. A
   * no-op when the element id doesn't exist (e.g. the caller raced a
   * doc rebuild that hasn't landed yet).
   */
  focusElement: (elementId: string, atStart?: boolean) => void;
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

/** The `@token` query under the caret, or `null` when the caret isn't
 *  immediately after an unbroken `@run` (scans left from `caretOffset` for
 *  the nearest `@` with no whitespace in between — matches legacy's
 *  "typing `@` opens the picker, keep typing to filter" UX without needing
 *  a keydown pre-hook, since a real text-node caret offset is available). */
function detectInlineMentionQuery(text: string, caretOffset: number): string | null {
  for (let i = caretOffset - 1; i >= 0; i -= 1) {
    const ch = text[i];
    if (ch === '@') return text.slice(i + 1, caretOffset);
    if (/\s/.test(ch)) return null;
  }
  return null;
}

interface ScriptElementViewRefs {
  formatRef: MutableRefObject<SceneFormat>;
  blockIndexBaseRef: MutableRefObject<number>;
  onTickClickRef: MutableRefObject<((elementId: string, shiftKey: boolean) => void) | undefined>;
  selectedElementIdsRef: MutableRefObject<Set<string> | undefined>;
  draggingElementIdRef: MutableRefObject<string | null | undefined>;
  dropElementEdgeRef: MutableRefObject<{ elementId: string; edge: 'top' | 'bottom' } | null | undefined>;
  onElementDragStartRef: MutableRefObject<((elementId: string) => void) | undefined>;
  onElementDragOverRef: MutableRefObject<((elementId: string, edge: 'top' | 'bottom') => void) | undefined>;
  onElementDropRef: MutableRefObject<((elementId: string, edge: 'top' | 'bottom') => void) | undefined>;
  onElementDragEndRef: MutableRefObject<(() => void) | undefined>;
  onElementContextMenuRef: MutableRefObject<
    ((elementId: string, x: number, y: number) => void) | undefined
  >;
  /**
   * Set while a press LANDED ON THE DRAG GRIP. The grip deliberately does NOT
   * preventDefault its mousedown (that would cancel the native drag — the whole
   * reason reorder was broken), so the browser still drops the caret into that
   * row as a side effect. Focusing a character/transition row auto-opens its cue
   * picker, so grabbing the grip of a character line popped the dropdown. This
   * flag lets the selection handler skip that one picker-open.
   */
  gripPressRef: MutableRefObject<boolean>;
}

/** The row DOM (spec D5): gutter [num + 4-dot drag handle] + tick + content. */
function ScriptElementView({ node, editor, getPos }: NodeViewProps, refs: ScriptElementViewRefs) {
  const attrs = node.attrs as ScriptElementAttrs;

  // Perf (M2 item 1c): re-rendering EVERY row on EVERY transaction (M0/M1's
  // "keep simple" approach) meant a pure text keystroke forced N React
  // re-renders for an N-element scene. All of our actual order-changing
  // pathways (Enter/Backspace locally, or ANY remote/reorder/cross-scene op)
  // either change `doc.childCount` (insert/delete) or land through
  // `applyExternalElements`'s `externalSync`-tagged transaction (moves,
  // remote ops, drag-drop, conflict rebuilds — see module doc) — so gating
  // the re-render on "childCount changed OR this was an externalSync
  // transaction" is a cheap, correct signal that skips the common case (a
  // plain keystroke changes neither) without missing any row-order change.
  const [, bumpTick] = useState(0);
  const lastChildCountRef = useRef(editor.state.doc.childCount);
  useEffect(() => {
    const rerender = ({ transaction }: { transaction: Transaction }) => {
      const cc = editor.state.doc.childCount;
      const forced = transaction.getMeta('externalSync') || transaction.getMeta('propsSync');
      if (forced || cc !== lastChildCountRef.current) {
        lastChildCountRef.current = cc;
        bumpTick((t) => t + 1);
      }
    };
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

  const isAsian = refs.formatRef.current === 'asian';
  const lineClass = isAsian ? ASIAN_LINE_CLASS[attrs.elType] : HOLLYWOOD_LINE_CLASS[attrs.elType];
  // Asian ornaments (spec D5, M3 item 1): a leading `△` (action) or trailing
  // fullwidth `：` (character) mark, rendered OUTSIDE the contentDOM — see
  // `AsianLayout.tsx`'s `ASIAN_PREFIX`/`ASIAN_SUFFIX` (single source, no
  // second hand-kept copy). `undefined` for every other type, same as legacy.
  const prefix = isAsian ? ASIAN_PREFIX[attrs.elType] : undefined;
  const suffix = isAsian ? ASIAN_SUFFIX[attrs.elType] : undefined;

  const onTickClick = refs.onTickClickRef.current;
  const selected = refs.selectedElementIdsRef.current?.has(attrs.id) ?? false;

  const draggingElementId = refs.draggingElementIdRef.current ?? null;
  const dragEnabled = !!refs.onElementDragStartRef.current;
  const dropEdge =
    refs.dropElementEdgeRef.current?.elementId === attrs.id
      ? refs.dropElementEdgeRef.current.edge
      : null;
  const dropClass = dropEdge === 'top' ? ' drop-top' : dropEdge === 'bottom' ? ' drop-bottom' : '';

  const onRowDragOver = (e: ReactDragEvent<HTMLDivElement>) => {
    const onOver = refs.onElementDragOverRef.current;
    // NO logging in here: dragover fires ~60×/s and console.log in that hot path
    // is itself a stutter source (it also drowned the console).
    if (!refs.draggingElementIdRef.current || !onOver) return;
    e.preventDefault();
    onOver(attrs.id, elementEdgeFromPointer(e.currentTarget, e.clientY));
  };
  const onRowDrop = (e: ReactDragEvent<HTMLDivElement>) => {
    const onDropCb = refs.onElementDropRef.current;
    console.log('[DRAG-DBG] DROP fired on row', attrs.id, 'draggingRef=', refs.draggingElementIdRef.current, 'cb=', !!onDropCb);
    if (!refs.draggingElementIdRef.current || !onDropCb) return;
    e.preventDefault();
    onDropCb(attrs.id, elementEdgeFromPointer(e.currentTarget, e.clientY));
  };
  // Right-click a row → the scene editor's context menu (delete block / delete
  // scene / move scene). Overrides the browser menu even inside the editable
  // content so the writer can delete the block they clicked (matches the heading
  // row). No-op when the callback is unwired (read-only embeds).
  const onRowContextMenu = (e: ReactMouseEvent<HTMLDivElement>) => {
    const cb = refs.onElementContextMenuRef.current;
    if (!cb) return;
    e.preventDefault();
    cb(attrs.id, e.clientX, e.clientY);
  };

  // Gutter + tick + content — the SAME three children in both formats;
  // what differs is what wraps them (see the isAsian branch below).
  const gutter = (
    <div className="mh-el-gutter" contentEditable={false}>
      <span className="mh-el-num">{displayIndex}</span>
      <button
        type="button"
        className={`mh-el-drag${draggingElementId === attrs.id ? ' dragging' : ''}`}
        contentEditable={false}
        tabIndex={-1}
        aria-label="Drag to reorder"
        title="Move paragraph"
        draggable={dragEnabled}
        // Flag the press so the selection handler can skip auto-opening the
        // character/transition cue picker for the caret this press drops into
        // the row (see gripPressRef's doc). We must NOT preventDefault here —
        // that cancels the native drag.
        onMouseDown={() => {
          refs.gripPressRef.current = true;
          // Self-clear: a grip press that never moves the caret would otherwise
          // leave the flag armed and swallow the NEXT real click's picker. PM
          // observes selection changes off a `selectionchange` listener (async),
          // so this has to outlive a microtask — 250ms is far longer than that
          // and far shorter than a human's next deliberate click.
          setTimeout(() => {
            refs.gripPressRef.current = false;
          }, 250);
        }}
        // NOTE: do NOT preventDefault on mousedown here — on a draggable element
        // that also cancels the browser's native drag gesture, so onDragStart
        // would never fire and the grip couldn't drag (the scene handle works
        // precisely because it has no mousedown guard). The grip is
        // contentEditable=false, so a plain mousedown won't corrupt the doc.
        onDragStart={
          dragEnabled
            ? (e: ReactDragEvent<HTMLButtonElement>) => {
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', attrs.id);
                // DEFER the state update by a tick. Calling it synchronously
                // re-renders this very NodeView (SceneBlock state → the
                // propsSync no-op transaction → PM rebuilds the row DOM, and the
                // grip's own `.dragging` class flips), and replacing the element
                // the browser is mid-drag on makes it ABORT the drag: dragend
                // fired immediately and every later dragover/drop then saw
                // draggingElementId=null and bailed. Proven live via [DRAG-DBG]
                // tracing (mousedown → DRAGSTART → dragend, all before any
                // dragover). One tick is enough for the browser to take its drag
                // snapshot first.
                const id = attrs.id;
                setTimeout(() => refs.onElementDragStartRef.current?.(id), 0);
              }
            : undefined
        }
        onDragEnd={dragEnabled ? () => refs.onElementDragEndRef.current?.() : undefined}
      >
        <span className="mh-el-dot" />
        <span className="mh-el-dot" />
        <span className="mh-el-dot" />
        <span className="mh-el-dot" />
      </button>
    </div>
  );
  const tick = onTickClick ? (
    <button
      type="button"
      className={`mh-el-tick tick-btn t-${attrs.elType}${selected ? ' selected' : ''}`}
      contentEditable={false}
      tabIndex={-1}
      aria-pressed={selected ? 'true' : 'false'}
      aria-label="Select element"
      data-tick-id={attrs.id}
      onMouseDown={(e: ReactMouseEvent) => e.preventDefault()}
      onClick={(e: ReactMouseEvent) => onTickClick(attrs.id, e.shiftKey)}
    />
  ) : (
    <span className={`mh-el-tick t-${attrs.elType}`} contentEditable={false} aria-hidden="true" />
  );
  const content = (
    <NodeViewContent
      as="div"
      className={`mh-el-editable mh-el-line ${lineClass}`}
      data-el-type={attrs.elType}
      data-el-id={attrs.id}
    />
  );

  if (isAsian) {
    // `.as-row.as-row-<type>[data-el-type]` wraps [prefix] + the SAME
    // `.mh-el-row` Hollywood renders bare + [suffix] — byte-identical to
    // `AsianLayout.tsx`'s `<div class="as-row..."><PageSeam/>?<span
    // as-prefix/><ElementLine/><span as-suffix/></div>` nesting (minus the
    // seam, which is a decoration — see pageSeamPlugin.ts — not JSX here).
    return (
      <NodeViewWrapper as="div" className={`as-row as-row-${attrs.elType}`} data-el-type={attrs.elType}>
        {prefix && (
          <span className="as-mark as-prefix" contentEditable={false} aria-hidden="true">
            {prefix}
          </span>
        )}
        <div
          className={`mh-el-row${dropClass}`}
          onDragOver={onRowDragOver}
          onDrop={onRowDrop}
          onContextMenu={onRowContextMenu}
        >
          {gutter}
          {tick}
          {content}
        </div>
        {suffix && (
          <span className="as-mark as-suffix" contentEditable={false} aria-hidden="true">
            {suffix}
          </span>
        )}
      </NodeViewWrapper>
    );
  }

  return (
    <NodeViewWrapper
      as="div"
      className={`mh-el-row${dropClass}`}
      onDragOver={onRowDragOver}
      onDrop={onRowDrop}
      onContextMenu={onRowContextMenu}
    >
      {gutter}
      {tick}
      {content}
    </NodeViewWrapper>
  );
}

export const TipTapSceneEditor = forwardRef<TipTapSceneEditorHandle, TipTapSceneEditorProps>(
  function TipTapSceneEditor(
    {
      initialElements,
      format,
      blockIndexBase = 0,
      dispatchOps,
      onFocusCursor,
      onExitEditing,
      onTickClick,
      selectedElementIds,
      draggingElementId,
      dropElementEdge,
      onElementDragStart,
      onElementDragOver,
      onElementDrop,
      onElementDragEnd,
      onElementContextMenu,
      onSlashChange,
      slashMenu,
      onMentionOpen,
      onMentionClose,
      mentionMenu,
      pageSeams,
      mentionCandidates,
    },
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

    const onExitEditingRef = useRef(onExitEditing);
    onExitEditingRef.current = onExitEditing;

    // M2 prop refs — kept fresh every render, read inside the NodeView
    // (gutter/tick/drag) and the menu-bridge keymap extension.
    const onTickClickRef = useRef(onTickClick);
    onTickClickRef.current = onTickClick;
    const selectedElementIdsRef = useRef(selectedElementIds);
    selectedElementIdsRef.current = selectedElementIds;
    const draggingElementIdRef = useRef(draggingElementId);
    draggingElementIdRef.current = draggingElementId;
    const dropElementEdgeRef = useRef(dropElementEdge);
    dropElementEdgeRef.current = dropElementEdge;
    const onElementDragStartRef = useRef(onElementDragStart);
    onElementDragStartRef.current = onElementDragStart;
    const onElementDragOverRef = useRef(onElementDragOver);
    onElementDragOverRef.current = onElementDragOver;
    const onElementDropRef = useRef(onElementDrop);
    onElementDropRef.current = onElementDrop;
    const onElementDragEndRef = useRef(onElementDragEnd);
    onElementDragEndRef.current = onElementDragEnd;
    // True only between a drag-grip mousedown and the selection change it causes.
    const gripPressRef = useRef(false);
    const onElementContextMenuRef = useRef(onElementContextMenu);
    onElementContextMenuRef.current = onElementContextMenu;
    const onSlashChangeRef = useRef(onSlashChange);
    onSlashChangeRef.current = onSlashChange;
    const onMentionOpenRef = useRef(onMentionOpen);
    onMentionOpenRef.current = onMentionOpen;
    const onMentionCloseRef = useRef(onMentionClose);
    onMentionCloseRef.current = onMentionClose;
    const mentionMenuRef = useRef<MenuBridge | null>(mentionMenu ?? null);
    mentionMenuRef.current = mentionMenu ?? null;
    const slashMenuRef = useRef<MenuBridge | null>(slashMenu ?? null);
    slashMenuRef.current = slashMenu ?? null;
    // M3 prop refs — read fresh by the decoration plugins' `decorations(state)`
    // (called by PM on every transaction; see pageSeamPlugin.ts /
    // mentionDecorationPlugin.ts's module docs for why a live ref beats
    // threading these through plugin state).
    const pageSeamsRef = useRef<PageSeamMap>(pageSeams ?? new Map());
    pageSeamsRef.current = pageSeams ?? new Map();
    const mentionCandidatesRef = useRef<string[]>(mentionCandidates ?? []);
    mentionCandidatesRef.current = mentionCandidates ?? [];

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
      const refs: ScriptElementViewRefs = {
        formatRef,
        blockIndexBaseRef,
        onTickClickRef,
        selectedElementIdsRef,
        draggingElementIdRef,
        dropElementEdgeRef,
        onElementDragStartRef,
        onElementDragOverRef,
        onElementDropRef,
        onElementDragEndRef,
        onElementContextMenuRef,
        gripPressRef,
      };
      const ViewNode = ScriptElementNode.extend({
        addNodeView() {
          return ReactNodeViewRenderer((props: NodeViewProps) => ScriptElementView(props, refs));
        },
      });
      const menuBridgeKeymap = createMenuBridgeKeymap({ mentionMenuRef, slashMenuRef });
      const pageSeamExtension = createPageSeamExtension(pageSeamsRef);
      const mentionDecorationExtension = createMentionDecorationExtension(mentionCandidatesRef);
      return [
        ScriptDocument,
        ScriptText,
        ViewNode,
        menuBridgeKeymap,
        ScriptKeymap,
        pageSeamExtension,
        mentionDecorationExtension,
      ];
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
        // Element reorder is driven by the NodeView's OWN React DnD (grip →
        // onDragStart, row → onDragOver/onDrop on .mh-el-row). ProseMirror's
        // built-in DnD otherwise fights it and wins: its dragstart sets
        // `view.dragging` for the grabbed node and its drop then moves that
        // slice — which lands as a mapper-derived `insert` with no element_id
        // and the ops endpoint 422s ("invalid_payload: insert requires
        // element_id"), with the doc already corrupted. Tell PM to ignore drags
        // that originate from our grip or land on our rows. Returning true only
        // makes PM skip ITS handler — it does NOT preventDefault (see
        // prosemirror-view runCustomHandler), so the native drag + our React
        // handlers still run.
        handleDOMEvents: {
          dragstart: (_view, event) => hitsSelector(event.target, '.mh-el-drag'),
          drop: (_view, event) => hitsSelector(event.target, '.mh-el-row'),
        },
      },
      onUpdate: ({ editor: ed, transaction }) => {
        // Our own applyExternalElements/retypeElement/replaceElementText
        // transaction — never re-emit.
        if (transaction.getMeta('externalSync')) return;

        const next = docToElements(ed.state.doc);

        // Never dispatch mid-composition (Chinese/Japanese/Korean IME) — just
        // remember the latest snapshot; the compositionend listener below
        // (or the next post-composition onUpdate) drives the actual flush.
        if (ed.view.composing) {
          pendingNextRef.current = next;
          return;
        }

        // M2 — slash menu: re-evaluate the caret's own line on every text
        // change, mirroring legacy's handleInput (a fresh '/' opens/updates
        // the picker; anything else, when it was open on this line, closes
        // it — SceneBlock's onSlashChange handler owns that branch).
        const ctx = getElementCtx(ed.state.selection.$from);
        if (ctx) {
          const text = ctx.node.textContent;
          onSlashChangeRef.current?.(ctx.node.attrs.id as string, text.startsWith('/') ? text.slice(1) : null);

          // M2 — inline mentions: a non-character line's caret sitting right
          // after an unbroken `@run` opens/updates the picker; otherwise
          // (deleted the `@`, moved off the run) close it. Character-type
          // lines are driven by onSelectionUpdate instead (query = whole
          // line, updates on focus AND on every keystroke since typing also
          // moves the selection).
          if ((ctx.node.attrs.elType as ElementType) !== 'character') {
            const query = detectInlineMentionQuery(text, ctx.localOffset);
            if (query !== null) {
              onMentionOpenRef.current?.(ctx.node.attrs.id as string, 'inline', query);
            } else {
              onMentionCloseRef.current?.();
            }
          }
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

        // M2 — character-cue picker: focusing (or editing) a character-type
        // line opens/updates the cue picker with the WHOLE line as the
        // query, mirroring legacy's handleFocus. Moving to a different line
        // (of any type) closes whatever mention/cue was open — legacy closes
        // unconditionally on a focus change to a different element, not just
        // when leaving a character line. `onMentionClose` is safe to call
        // spuriously (SceneBlock's `setMention(null)` no-ops via React's
        // same-value state bailout when nothing was open).
        if (gripPressRef.current) {
          // This caret move is the side effect of grabbing the row's drag grip,
          // not the writer clicking into the line — don't pop the cue picker (a
          // character row's grip would otherwise always open the dropdown). The
          // grip can't preventDefault its mousedown to stop the caret move: that
          // cancels the native drag, which is what broke reorder in the first
          // place. Only a real click on the LINE should open the picker.
          gripPressRef.current = false;
          onMentionCloseRef.current?.();
        } else if (ctx && (ctx.node.attrs.elType as ElementType) === 'character') {
          onMentionOpenRef.current?.(id as string, 'character', ctx.node.textContent);
        } else if (ctx && (ctx.node.attrs.elType as ElementType) === 'transition') {
          // Transition preset picker (laper parity): a focused transition line
          // offers CUT TO: / FADE TO: / … — same open/filter/replace contract
          // as the character cue, so it rides the whole mention pipeline.
          onMentionOpenRef.current?.(id as string, 'transition', ctx.node.textContent);
        } else {
          onMentionCloseRef.current?.();
        }
      },
      onBlur: () => {
        clearDebounce();
        flushPending();
        // Editing surface lost focus → relax the shell's data-editing emphasis.
        onExitEditingRef.current?.();
      },
    });

    // Flush on unmount too (view-switch guard, mirrors SceneBlock's own
    // unmount-flush for its input debounce).
    useEffect(() => () => flushPending(), [flushPending]);

    // M2 — the copilot tick's `.selected` state and the drag handle's
    // dragging/drop-edge classes live in refs (not React props on the
    // NodeView, which only ever receives `{node, editor, getPos, ...}` from
    // `ReactNodeViewRenderer`), and each row's own re-render is gated on the
    // editor's 'transaction' event (perf — see `ScriptElementView`'s module
    // doc). None of THOSE three inputs changing (a tick click, a drag
    // start/end) is itself a PM transaction, so without this they'd never
    // repaint. Dispatching a no-op transaction tagged `propsSync` forces
    // exactly one repaint per actual change — `onUpdate` safely ignores it
    // (no doc change → `mapDocChange` returns zero ops) and Tiptap's own
    // `update` event only fires when `docChanged`, so this never reaches
    // the ops pipeline. `slashMenu`/`mentionMenu` are deliberately NOT here:
    // they drive popups SceneBlock renders outside this editor, not the row
    // DOM, and including them would re-fire this on every keystroke of an
    // open menu's filter — exactly the per-keystroke repaint cost item 1c
    // eliminated.
    //
    // M3: `pageSeams`/`mentionCandidates` ride the SAME dispatch — neither
    // is a React NodeView prop (both are read straight off a ref by the
    // decoration plugins' `decorations(state)`, see pageSeamPlugin.ts /
    // mentionDecorationPlugin.ts), but PM only re-calls `decorations(state)`
    // after a transaction, so a prop change with no doc edit of its own
    // (a re-measured seam map, an updated cast list) needs this same forced
    // dispatch to actually repaint. Both change far less often than a
    // keystroke (a ResizeObserver-throttled layout pass; a cast-list edit),
    // so folding them into the existing per-row repaint gate is an
    // acceptable one-more-repaint cost, not a new per-keystroke one.
    useEffect(() => {
      if (!editor) return;
      editor.view.dispatch(editor.state.tr.setMeta('propsSync', true));
    }, [editor, selectedElementIds, draggingElementId, dropElementEdge, pageSeams, mentionCandidates]);

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

    const retypeElement = useCallback(
      (elementId: string, type: ElementType, clearText = false) => {
        if (!editor) return;
        const target = findNodeById(editor.state.doc, elementId);
        if (!target) return;
        const { pos, node } = target;
        let tr = editor.state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, elType: type });
        if (clearText) {
          const textLen = node.textContent.length;
          if (textLen > 0) tr = tr.delete(pos + 1, pos + 1 + textLen);
          tr = tr.setSelection(TextSelection.create(tr.doc, pos + 1));
        }
        tr.setMeta('externalSync', true);
        editor.view.dispatch(tr);
      },
      [editor],
    );

    const replaceElementText = useCallback(
      (elementId: string, text: string) => {
        if (!editor) return;
        const target = findNodeById(editor.state.doc, elementId);
        if (!target) return;
        const { pos, node } = target;
        let tr = editor.state.tr;
        const len = node.textContent.length;
        if (len > 0) tr = tr.delete(pos + 1, pos + 1 + len);
        if (text.length > 0) tr = tr.insert(pos + 1, editor.state.schema.text(text));
        tr = tr.setSelection(TextSelection.create(tr.doc, pos + 1 + text.length));
        tr.setMeta('externalSync', true);
        editor.view.dispatch(tr);
      },
      [editor],
    );

    const focusElement = useCallback(
      (elementId: string, atStart = false) => {
        if (!editor) return;
        const target = findNodeById(editor.state.doc, elementId);
        if (!target) return;
        const pos = atStart ? target.pos + 1 : target.pos + 1 + target.node.content.size;
        editor.chain().focus().setTextSelection(pos).run();
      },
      [editor],
    );

    useImperativeHandle(
      ref,
      () => ({ applyExternalElements, retypeElement, replaceElementText, focusElement }),
      [applyExternalElements, retypeElement, replaceElementText, focusElement],
    );

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
