/**
 * SceneBlock — one scene's container + live element editing (spec v3 §3.2/§3.3).
 *
 * Owns exactly one scene's write path via `useSceneSync(scene)` and turns
 * keystrokes into anchored ops through the pure editorMachine:
 *  - Tab / Shift-Tab cycle the element type (update op)
 *  - Enter inserts the next element (anchored after the current id)
 *  - Backspace at the start of an EMPTY line deletes it and pulls the cursor up
 * After each transition the optimistic elements are dispatched and, on the next
 * frame, focus jumps to the new cursor's `[data-el-id]`. IME composition is
 * respected (the machine never fires mid-composition), and paste splits plain
 * text on newlines into a chain of anchored action inserts.
 *
 * The scene head row (INT/EXT · location · time) writes through updateSceneMeta,
 * debounced 600ms; text input debounces 500ms. The `::` drag handle is a
 * render-only placeholder here — real reordering is Task 10.
 */
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import {
  onBackspaceAtStart,
  onEnter,
  onShiftTab,
  onTab,
  type CursorState,
  type MachineResult,
} from '../editorMachine';
import { applyLocal, buildInverse } from '../opBuilder';
import { newElementId, updateSceneMeta } from '../sceneService';
import {
  buildPolishOps,
  requestCopilotOps,
  CopilotDisabledError,
  OpRejectedError,
} from '../copilotService';
import type { ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';
import { useSceneSync, type RemoteOpRow } from '../useSceneSync';
import { HollywoodLayout } from '../render/HollywoodLayout';
import { AsianLayout } from '../render/AsianLayout';
import { MentionNamesContext, type LineMention } from '../render/layoutShared';
import { MentionCombobox, filterMentionCandidates } from './MentionCombobox';
import { SlashMenu, SLASH_ITEMS, filterSlashItems, type SlashItem } from './SlashMenu';
import { CopilotCard, type CopilotPhase } from './CopilotCard';
import { EmptySceneHint } from './EmptyStates';
import { isTiptapEnabled } from '../tiptap/flag';
import { TipTapSceneEditor, type TipTapSceneEditorHandle } from '../tiptap/TipTapSceneEditor';
import type { MenuBridge } from '../tiptap/menuKeymap';
import type { EditorFormat } from '../useEditorState';
import type { SaveState } from '../useSceneSync';
import { ScenePresenceBadge } from '../collab/ScenePresenceBadge';
import type { PresenceUser } from '../collab/useScriptPresence';
import { UiSelect } from '../../components/ui';

/** A scene's save status lifted to the shell for the aggregate SaveIndicator. */
export interface SceneSyncStatus {
  saveState: SaveState;
  resolveConflict: (choice: 'mine' | 'theirs') => void;
}

/**
 * Drag + keyboard scene-reorder wiring, owned by the shell (which knows the full
 * ordered scene list and calls moveScene). One handle drives both paths: HTML5
 * DnD on the `::` grip, and Alt+Arrow while the grip is focused (spec §3.5).
 */
export interface SceneReorderApi {
  /** The scene currently being dragged (for aria-grabbed + drop-target styling). */
  draggingId: string | null;
  /** The active drop target: which scene and which edge the line will sit on. */
  dropTarget: { sceneId: string; edge: 'before' | 'after' } | null;
  onDragStart: (sceneId: string) => void;
  onDragEnd: () => void;
  onDragOver: (sceneId: string, edge: 'before' | 'after') => void;
  onDrop: (sceneId: string, edge: 'before' | 'after') => void;
  onKeyboardMove: (sceneId: string, direction: 'up' | 'down') => void;
}

/** An open @-mention / character-cue / transition-preset picker anchored to
 *  one element line. */
interface MentionState {
  elementId: string;
  /** 'inline' = typed `@` inside a line; 'character' = a focused character
   *  cue; 'transition' = a focused transition line (preset picker). */
  kind: 'inline' | 'character' | 'transition';
  query: string;
  position?: { top: number; left: number };
}

/** Industry transition presets (laper parity) — offered whenever a transition
 *  line is focused; the line text filters and Enter replaces the whole line. */
const TRANSITION_PRESETS = ['CUT TO:', 'FADE TO:', 'DISSOLVE TO:', 'FADE IN:', 'FADE OUT.'];

/** A toolbar-issued retype of the focused element, routed to the owning scene. */
export interface TypeCommand {
  sceneId: string;
  elementId: string;
  type: ElementType;
  /** Bump to re-fire even when the payload is unchanged. */
  nonce: number;
}

const INT_EXT_OPTIONS = ['INT', 'EXT', 'INT/EXT'];
const TIME_OPTIONS = ['DAY', 'NIGHT', 'DAWN', 'DUSK', 'CONTINUOUS'];

/**
 * Compose the read-mode scene heading from the meta fields, per layout engine:
 *  - Hollywood: a slug line `INT. BLANK STUDIO - NIGHT` (uppercase location,
 *    dot after INT/EXT, dash before the time).
 *  - Asian: a middot-joined manuscript heading `内景 · 地点 · 夜` (no forced
 *    uppercasing — CJK has no case), sitting after the numbered badge.
 * Missing parts drop out cleanly; an all-empty heading returns '' so the caller
 * can render the "set heading" placeholder instead.
 */
function formatSceneHeading(meta: SceneMeta, format: EditorFormat): string {
  const ie = meta.heading_int_ext.trim();
  const loc = meta.location_text.trim();
  const time = meta.time_of_day.trim();
  if (format === 'asian') {
    return [ie, loc, time].filter((p) => p.length > 0).join(' · ');
  }
  const head = ie ? `${ie}.` : '';
  const locTime = [loc.toUpperCase(), time].filter((p) => p.length > 0).join(' - ');
  return [head, locTime].filter((p) => p.length > 0).join(' ');
}

const INPUT_DEBOUNCE_MS = 500;
const META_DEBOUNCE_MS = 600;

interface SceneMeta {
  heading_int_ext: string;
  location_text: string;
  time_of_day: string;
}

export interface SceneBlockProps {
  scene: SceneDoc;
  index: number;
  /** Cumulative block-index offset from `sceneBlockBases` (A1 continuous
   *  numbering): this scene's heading renders as `blockIndexBase + 1` and its
   *  elements as `blockIndexBase + 2, +3, ...`. Defaults to 0 so a bare
   *  SceneBlock (e.g. in isolation tests) still numbers from 1. */
  blockIndexBase?: number;
  /** Reports the focused element up so the toolbar/statistics can track the cursor. */
  onFocusElement?: (cursor: CursorState) => void;
  /** Toolbar retype command targeted (by sceneId) at this block's focused element. */
  typeCommand?: TypeCommand;
  /** Layout engine: 'hollywood' (default) or 'asian'. Both consume the same elements. */
  format?: EditorFormat;
  /** Distinct CAST names for the @-mention / character-cue picker (script-wide). */
  mentionCandidates?: string[];
  /** Reports this scene's save state up so the shell can aggregate it. */
  onSyncStateChange?: (sceneId: string, status: SceneSyncStatus) => void;
  /** Drag/keyboard reorder wiring (Task 10); absent = reorder disabled. */
  reorder?: SceneReorderApi;
  /** Called when Esc leaves an element line so the shell can clear its
   * `data-editing` styling hook (toolbar emphasis), not any focus/a11y state. */
  onExitEditing?: () => void;
  /** The scene that currently owns the copilot card (shell keeps it to one). */
  copilotActiveSceneId?: string | null;
  /** Notifies the shell which scene (if any) now holds a copilot selection. */
  onCopilotActivate?: (sceneId: string | null) => void;
  /** Reports this scene's live (optimistic) elements up (debounced 1s) so the
   *  shell can derive Statistics / rail entities from in-flight edits. */
  onElementsChange?: (sceneId: string, elements: ScriptElement[]) => void;
  /** Other collaborators (self excluded) currently focused on this scene (P5). */
  focusPresence?: PresenceUser[];
  /** Local user's actor id, so remote self-echoed ops are dropped (C2). */
  selfActorId?: string | null;
  /** Registers this scene's applyRemoteOps with the shell's op-stream router
   *  (called with null on unmount). */
  onRegisterRemoteApply?: (sceneId: string, apply: ((row: RemoteOpRow) => void) | null) => void;
  /** True when a remote op for this scene arrived while it was unmounted
   *  (windowed out); the block refetches its scene on (re)mount to shed the
   *  stale snapshot, then calls onRemoteStaleHandled to clear the flag (C2). */
  remoteStale?: boolean;
  onRemoteStaleHandled?: (sceneId: string) => void;
  /** ── Cross-scene paragraph drag (shell-coordinated) ──
   * The shell holds which element is being dragged from which scene; every
   * OTHER SceneBlock treats that as an external drag it can accept. */
  elementDrag?: { sceneId: string; element: ScriptElement } | null;
  /** Report drag start/finish up so the shell can broadcast the drag. */
  onElementDragBegin?: (sceneId: string, element: ScriptElement) => void;
  onElementDragDone?: () => void;
  /** Ask the shell to delete the dragged element from its SOURCE scene after
   *  this (target) scene inserted it. */
  onCrossSceneDelete?: (sceneId: string, elementId: string) => void;
  /** Registers a dispatcher the shell can use to apply ops to this scene from
   *  outside (the cross-scene delete); called with null on unmount. */
  onRegisterExternalOps?: (sceneId: string, fn: ((ops: ElementOp[]) => void) | null) => void;
  /** Paged mode v2: page seams keyed by element id (rendered before that row). */
  pageSeams?: Map<string, { page: number; filler: number }>;
  /** TipTap surface switch RESOLVED BY THE SHELL (admin module registry →
   *  localStorage emergency override → env dev fallback). When omitted the
   *  block resolves locally (override/env only) — keeps standalone renders
   *  and existing tests working. */
  tiptapSurface?: boolean;
}

/** Which half of a block the pointer is over → the drop edge. */
function edgeFromPointer(el: HTMLElement, clientY: number): 'before' | 'after' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}

export function SceneBlock({
  scene,
  index,
  blockIndexBase = 0,
  onFocusElement,
  typeCommand,
  format = 'hollywood',
  mentionCandidates = [],
  onSyncStateChange,
  reorder,
  onExitEditing,
  copilotActiveSceneId,
  onCopilotActivate,
  onElementsChange,
  focusPresence,
  selfActorId,
  onRegisterRemoteApply,
  remoteStale,
  onRemoteStaleHandled,
  elementDrag,
  onElementDragBegin,
  onElementDragDone,
  onCrossSceneDelete,
  onRegisterExternalOps,
  pageSeams,
  tiptapSurface,
}: SceneBlockProps) {
  const { t } = useTranslation();
  const sync = useSceneSync(scene, { selfActorId });
  const [focusedElementId, setFocusedElementId] = useState<string | null>(null);
  const [mention, setMention] = useState<MentionState | null>(null);
  // Mention nav state owned HERE (the combobox is presentational): the active
  // option index + the shared listbox id feed both the popup and the ARIA
  // combobox attributes that ride on the focused line.
  const mentionListId = useId();
  const [mentionActive, setMentionActive] = useState(0);
  // Copilot (Task 11): elements selected via their gutter ticks + this turn's
  // applied-edit count and the inverse batch that undoes them.
  const [copilotSelection, setCopilotSelection] = useState<string[]>([]);
  const [copilotAnchor, setCopilotAnchor] = useState<string | null>(null);
  const [copilotEdits, setCopilotEdits] = useState<number | null>(null);
  const [copilotInverse, setCopilotInverse] = useState<ElementOp[] | null>(null);
  // Copilot-OWNED lifecycle: 'applying' only between our own dispatch and the
  // FIRST terminal saveState after it. Deriving the phase from the shared
  // scene saveState alone misreports unrelated saves (a normal edit after a
  // polish would flip the card back to "Polishing…") — review finding F4-2.
  const [copilotStatus, setCopilotStatus] = useState<'applying' | 'done' | 'failed' | null>(null);
  useEffect(() => {
    if (copilotStatus !== 'applying') return;
    if (sync.saveState === 'saved') setCopilotStatus('done');
    else if (sync.saveState === 'retrying' || sync.saveState === 'conflict') {
      setCopilotStatus('failed');
    }
  }, [copilotStatus, sync.saveState]);
  // Free-text reconciler (Task 8): the instruction box, the in-flight LLM
  // round-trip ('thinking' precedes any dispatch, so it is tracked separately
  // from the dispatch-driven copilotStatus), a pending proposal awaiting review,
  // the done-state summary, a 422 detail, and the 404 (flag-off) memory that
  // disables the box for the rest of this session.
  const [copilotInstruction, setCopilotInstruction] = useState('');
  const [copilotRequest, setCopilotRequest] = useState<'idle' | 'thinking'>('idle');
  const [copilotProposal, setCopilotProposal] = useState<{
    ops: ElementOp[];
    summary: string;
  } | null>(null);
  const [copilotSummary, setCopilotSummary] = useState<string | null>(null);
  const [copilotFailedDetail, setCopilotFailedDetail] = useState<string | null>(null);
  const [copilotDisabled, setCopilotDisabled] = useState(false);
  const [meta, setMeta] = useState<SceneMeta>({
    heading_int_ext: scene.heading_int_ext ?? '',
    location_text: scene.location_text ?? '',
    time_of_day: scene.time_of_day ?? '',
  });
  // Head row is dual-state (Task 4.5): a typographic slug by default, the three
  // selects only while editing. Entering focuses the INT/EXT select; blur out of
  // the row (or Esc) drops back to the read-mode slug.
  const [headingEditing, setHeadingEditing] = useState(false);
  // Element-level drag-to-reorder (hover-gutter 6-dot handle): the element being
  // dragged + the live drop target (which row + edge). Kept within this scene —
  // v1 does not support cross-scene element moves.
  const [draggingElementId, setDraggingElementId] = useState<string | null>(null);
  const [elementDropTarget, setElementDropTarget] = useState<{
    elementId: string;
    edge: 'top' | 'bottom';
  } | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const headRowRef = useRef<HTMLDivElement | null>(null);
  const headingDisplayRef = useRef<HTMLButtonElement | null>(null);
  const composingRef = useRef(false);
  // TipTap editing surface (flag-dark, spec D7): re-read per render (not a
  // frozen module const) so tests can `vi.stubEnv` it — see tiptap/flag.ts.
  const tiptapOn = tiptapSurface ?? isTiptapEnabled();
  const tiptapRef = useRef<TipTapSceneEditorHandle>(null);
  const elementsRef = useRef<ScriptElement[]>(sync.elements);
  const inputTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  // Text buffered behind each input debounce — flushed (not dropped) on unmount
  // so a view switch mid-keystroke never loses writing (spec §3.6 flush guard).
  const pendingInputRef = useRef<Record<string, string>>({});
  const pendingMetaRef = useRef<Partial<SceneMeta> | null>(null);
  // Latest sync/scene-id for the []-dep unmount flush (avoids stale closures).
  const syncRef = useRef(sync);
  syncRef.current = sync;
  const sceneIdRef = useRef(scene.id);
  sceneIdRef.current = scene.id;
  const onSyncStateChangeRef = useRef(onSyncStateChange);
  onSyncStateChangeRef.current = onSyncStateChange;
  const metaTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mentionRef = useRef<MentionState | null>(null);

  // Filtered candidates for the open picker; kept in a ref so the keydown
  // handler (a stable callback) reads the latest list without re-subscribing.
  // The transition preset picker rides the same pipeline with its own list.
  const activeMentionCandidates =
    mention?.kind === 'transition' ? TRANSITION_PRESETS : mentionCandidates;
  const mentionFiltered = useMemo(
    () => (mention ? filterMentionCandidates(activeMentionCandidates, mention.query) : []),
    [mention, activeMentionCandidates],
  );
  const mentionFilteredRef = useRef<string[]>(mentionFiltered);
  const mentionActiveRef = useRef(0);

  // ── Slash menu (`/` at block start → block-type picker) ──────────────────
  // Mirrors the mention picker's state/ref shape exactly: the open state +
  // filtered items live in state, and refs feed the stable keydown handler.
  const [slash, setSlash] = useState<{
    elementId: string;
    query: string;
    position?: { top: number; left: number };
  } | null>(null);
  const [slashActive, setSlashActive] = useState(0);
  const slashListId = useId();
  const slashRef = useRef<typeof slash>(null);
  const slashFiltered = useMemo(
    () => (slash ? filterSlashItems(SLASH_ITEMS, slash.query, t) : []),
    [slash, t],
  );
  const slashFilteredRef = useRef<SlashItem[]>(slashFiltered);
  const slashActiveRef = useRef(0);

  elementsRef.current = sync.elements;
  mentionRef.current = mention;
  mentionFilteredRef.current = mentionFiltered;
  mentionActiveRef.current = mentionActive;
  slashRef.current = slash;
  slashFilteredRef.current = slashFiltered;
  slashActiveRef.current = slashActive;

  // Reset the active option to the top whenever the picker opens or its filter
  // changes (typing narrows the list); nav-only changes must NOT reset it.
  useEffect(() => {
    setMentionActive(0);
  }, [mention?.elementId, mention?.query, mentionCandidates]);

  useEffect(() => {
    const inputTimers = inputTimersRef.current;
    return () => {
      // FLUSH, don't drop: a pending debounce at unmount (view switch, nav)
      // carries real keystrokes. dispatchOps still enqueues + fires the network
      // write after unmount (useSceneSync guards its setState via mountedRef).
      Object.values(inputTimers).forEach(clearTimeout);
      const pending = pendingInputRef.current;
      const ids = Object.keys(pending);
      if (ids.length > 0) {
        const ops: ElementOp[] = ids.map((elementId) => ({
          op: 'update',
          element_id: elementId,
          payload: { text: pending[elementId] },
        }));
        syncRef.current.dispatchOps(ops, applyLocal(elementsRef.current, ops));
        pendingInputRef.current = {};
      }
      onSyncStateChangeRef.current?.(sceneIdRef.current, {
        saveState: 'saved',
        resolveConflict: () => undefined,
      });
      if (metaTimerRef.current) clearTimeout(metaTimerRef.current);
      const metaPatch = pendingMetaRef.current;
      if (metaPatch) {
        pendingMetaRef.current = null;
        updateSceneMeta(sceneIdRef.current, {
          heading_int_ext: metaPatch.heading_int_ext ?? undefined,
          location_text: metaPatch.location_text ?? undefined,
          time_of_day: metaPatch.time_of_day ?? undefined,
        }).catch((err) => console.error('[SceneBlock] flush updateSceneMeta failed', err));
      }
    };
  }, []);

  // Apply a machine result: dispatch its ops (if any) and move focus to the new
  // cursor's element on the next frame, once the optimistic row has rendered.
  const applyResult = useCallback(
    (result: MachineResult) => {
      if (result.ops.length > 0) sync.dispatchOps(result.ops, result.localElements);
      const targetId = result.cursor.elementId;
      setFocusedElementId(targetId);
      if (targetId) {
        requestAnimationFrame(() => {
          const node = containerRef.current?.querySelector<HTMLElement>(
            `[data-el-id="${targetId}"]`,
          );
          node?.focus();
        });
      }
    },
    [sync],
  );

  // Caret-safe imperative write: the focused row is never repainted by React
  // (protects the caret), so a mention insertion writes the DOM node directly
  // and drops the caret at the end. Chips render when the row later settles.
  const setNodeText = useCallback((elementId: string, text: string) => {
    const node = containerRef.current?.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`);
    if (!node) return;
    node.textContent = text;
    // A select made from the popup's embedded search input leaves focus in
    // that input — reclaim it so the caret placement below lands visibly.
    node.focus();
    const sel = window.getSelection();
    if (!sel) return;
    const range = document.createRange();
    range.selectNodeContents(node);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }, []);

  const openMention = useCallback((elementId: string, kind: MentionState['kind']) => {
    const node = containerRef.current?.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`);
    const position = node ? { top: node.offsetTop + node.offsetHeight, left: node.offsetLeft } : undefined;
    setMention({ elementId, kind, query: '', position });
  }, []);

  const handleMentionSelect = useCallback(
    (name: string) => {
      const m = mentionRef.current;
      if (!m) return;
      let newText: string;
      if (m.kind !== 'inline') {
        // A character cue IS the name; a transition IS the preset — either
        // way the picked option replaces the whole line.
        newText = name;
      } else {
        const el = elementsRef.current.find((e) => e.id === m.elementId);
        const text = el?.text ?? '';
        const at = text.lastIndexOf('@');
        newText =
          at >= 0
            ? `${text.slice(0, at)}@${name} ${text.slice(at + 1 + m.query.length)}`
            : `${text}@${name} `;
      }
      const op: ElementOp = { op: 'update', element_id: m.elementId, payload: { text: newText } };
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
      setNodeText(m.elementId, newText);
      setMention(null);
    },
    [sync, setNodeText],
  );

  // Apply a slash-menu pick: retype the CURRENT block and clear the `/query`
  // text (both the model, via one update op, and the live DOM node — the
  // focused row is never repainted by React, same caret rule as mentions).
  const applySlash = useCallback(
    (type: ElementType) => {
      const s = slashRef.current;
      if (!s) return;
      const op: ElementOp = {
        op: 'update',
        element_id: s.elementId,
        payload: { type, text: '' },
      };
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
      setNodeText(s.elementId, '');
      setSlash(null);
    },
    [sync, setNodeText],
  );

  const handleKeyDown = useCallback(
    (elementId: string, e: KeyboardEvent<HTMLDivElement>) => {
      // Never intervene mid-IME-composition — let the browser compose.
      if (composingRef.current || e.nativeEvent.isComposing) return;
      const cursor: CursorState = { sceneId: scene.id, elementId, field: 'element' };
      const els = elementsRef.current;

      // While the mention picker is open on this line, it owns the nav keys.
      const mentionOpen = mentionRef.current;
      if (mentionOpen && mentionOpen.elementId === elementId) {
        const filtered = mentionFilteredRef.current;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (filtered.length > 0) {
            setMentionActive((prev) => (prev + 1 + filtered.length) % filtered.length);
          }
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (filtered.length > 0) {
            setMentionActive((prev) => (prev - 1 + filtered.length) % filtered.length);
          }
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          const active = mentionActiveRef.current;
          if (filtered.length > 0 && active >= 0 && active < filtered.length) {
            handleMentionSelect(filtered[active]);
          } else {
            setMention(null);
          }
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setMention(null);
          return;
        }
        // Transition preset picker: Tab keeps its REAL type-cycle semantics —
        // don't consume it here; the machine handler below retypes the block
        // and this picker closes on the resulting focus/type change.
        if (e.key === 'Tab' && mentionOpen.kind !== 'transition') {
          e.preventDefault();
          // Character-cue selector: Tab abandons the cue and reverts to action
          // (spec §3.2 laper behaviour); inline mention just closes.
          if (mentionOpen.kind === 'character') {
            const op: ElementOp = {
              op: 'update',
              element_id: elementId,
              payload: { type: 'action' },
            };
            sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
          }
          setMention(null);
          return;
        }
      }

      // While the slash menu is open on this line, it owns the nav keys.
      const slashOpen = slashRef.current;
      if (slashOpen && slashOpen.elementId === elementId) {
        const filtered = slashFilteredRef.current;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (filtered.length > 0) setSlashActive((prev) => (prev + 1) % filtered.length);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (filtered.length > 0) {
            setSlashActive((prev) => (prev - 1 + filtered.length) % filtered.length);
          }
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          const active = slashActiveRef.current;
          if (filtered.length > 0 && active >= 0 && active < filtered.length) {
            applySlash(filtered[active].type);
          } else {
            setSlash(null);
          }
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setSlash(null);
          return;
        }
      }

      // Typing `@` opens the inline picker; let the character itself be typed.
      if (e.key === '@') {
        openMention(elementId, 'inline');
        return;
      }

      // Esc leaves the element line: blur so Tab resumes the page's normal
      // (native) focus order, and tell the shell to clear its data-editing
      // styling hook that emphasizes the toolbar while a line is focused.
      if (e.key === 'Escape') {
        e.preventDefault();
        e.currentTarget.blur();
        onExitEditing?.();
        return;
      }

      if (e.key === 'Tab') {
        e.preventDefault();
        applyResult(e.shiftKey ? onShiftTab(els, cursor) : onTab(els, cursor));
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        applyResult(onEnter(els, cursor));
        return;
      }
      if (e.key === 'Backspace') {
        const sel = window.getSelection();
        const atStart = !!sel && sel.isCollapsed && sel.anchorOffset === 0;
        if (!atStart) return;
        const result = onBackspaceAtStart(els, cursor);
        if (result.ops.length === 0) return; // non-empty line: browser deletes a char
        e.preventDefault();
        applyResult(result);
      }
    },
    [scene.id, applyResult, sync, openMention, handleMentionSelect, applySlash, onExitEditing],
  );

  const handleInput = useCallback(
    (elementId: string, text: string) => {
      // Keep the open picker's filter in sync with the line as the writer types.
      const m = mentionRef.current;
      if (m && m.elementId === elementId) {
        if (m.kind === 'inline') {
          const at = text.lastIndexOf('@');
          if (at === -1) setMention(null);
          else setMention({ ...m, query: text.slice(at + 1) });
        } else {
          setMention({ ...m, query: text });
        }
      }

      // Slash menu: `/` at the START of a block opens the type picker; the
      // text after the slash is the live filter. Anything else closes it.
      if (text.startsWith('/')) {
        const node = containerRef.current?.querySelector<HTMLElement>(
          `[data-el-id="${elementId}"]`,
        );
        const position = node
          ? { top: node.offsetTop + node.offsetHeight, left: node.offsetLeft }
          : undefined;
        setSlashActive(0);
        setSlash({ elementId, query: text.slice(1), position });
      } else if (slashRef.current?.elementId === elementId) {
        setSlash(null);
      }

      const timers = inputTimersRef.current;
      pendingInputRef.current[elementId] = text;
      if (timers[elementId]) clearTimeout(timers[elementId]);
      timers[elementId] = setTimeout(() => {
        const op: ElementOp = { op: 'update', element_id: elementId, payload: { text } };
        const optimistic = applyLocal(elementsRef.current, [op]);
        sync.dispatchOps([op], optimistic);
        delete timers[elementId];
        delete pendingInputRef.current[elementId];
      }, INPUT_DEBOUNCE_MS);
    },
    [sync],
  );

  const handlePaste = useCallback(
    (elementId: string, e: React.ClipboardEvent<HTMLDivElement>) => {
      e.preventDefault();
      const raw = e.clipboardData.getData('text/plain');
      const lines = raw.split(/\r?\n/).filter((l) => l.trim() !== '');
      if (lines.length === 0) return;
      const ops: ElementOp[] = [];
      let anchor = elementId;
      for (const line of lines) {
        const id = newElementId();
        ops.push({
          op: 'insert',
          element_id: id,
          after_id: anchor,
          payload: { type: 'action', text: line },
        });
        anchor = id;
      }
      const optimistic = applyLocal(elementsRef.current, ops);
      sync.dispatchOps(ops, optimistic);
    },
    [sync],
  );

  const handleFocus = useCallback(
    (elementId: string) => {
      setFocusedElementId(elementId);
      onFocusElement?.({ sceneId: scene.id, elementId, field: 'element' });
      // Focusing a character cue opens the same picker (laper behaviour);
      // focusing any other row dismisses a picker left open elsewhere.
      const el = elementsRef.current.find((e) => e.id === elementId);
      if (el?.type === 'character') {
        openMention(elementId, 'character');
      } else if (el?.type === 'transition') {
        openMention(elementId, 'transition');
      } else {
        setMention((prev) => (prev && prev.elementId !== elementId ? null : prev));
      }
    },
    [onFocusElement, scene.id, openMention],
  );

  // Toolbar retype: when a command targets this scene, update the focused
  // element's type in place (only if it still exists in the optimistic view).
  const lastCommandNonceRef = useRef(0);
  useEffect(() => {
    if (!typeCommand || typeCommand.sceneId !== scene.id) return;
    if (typeCommand.nonce === lastCommandNonceRef.current) return;
    lastCommandNonceRef.current = typeCommand.nonce;
    const target = elementsRef.current.find((el) => el.id === typeCommand.elementId);
    if (!target) return;
    const op: ElementOp = {
      op: 'update',
      element_id: typeCommand.elementId,
      payload: { type: typeCommand.type },
    };
    // TipTap mode: apply the attrs-only transaction immediately (caret-
    // preserving) via the ref method; the ops dispatch below still lands
    // (data plane unchanged) and the M1 applyExternalElements effect finds
    // the doc already matches, so it no-ops rather than rebuilding.
    if (tiptapOn) tiptapRef.current?.retypeElement(typeCommand.elementId, typeCommand.type);
    sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
    // The toolbar button stole focus on click — hand it straight back to the
    // retyped line so the writer keeps typing (and the type-driven pickers,
    // e.g. the transition presets, open on the resulting selection update).
    if (tiptapOn) {
      tiptapRef.current?.focusElement(typeCommand.elementId);
    } else {
      requestAnimationFrame(() => {
        containerRef.current
          ?.querySelector<HTMLElement>(`[data-el-id="${typeCommand.elementId}"]`)
          ?.focus();
      });
    }
  }, [typeCommand, scene.id, sync, tiptapOn]);

  // Lift this scene's save state to the shell whenever it changes.
  useEffect(() => {
    onSyncStateChange?.(scene.id, {
      saveState: sync.saveState,
      resolveConflict: sync.resolveConflict,
    });
  }, [scene.id, sync.saveState, sync.resolveConflict, onSyncStateChange]);

  // Register this scene's applyRemoteOps with the shell's op-stream router so
  // realtime rows for this scene reach it; deregister on unmount / scene swap (C2).
  // scene.id is typed string but the scenes API returns it as a JSON number
  // (#1006), and the router's lookup key is String(record.scene_id) from the
  // realtime row — so we String() the registration key to match (mixed-type Map
  // keys silently miss).
  useEffect(() => {
    if (!onRegisterRemoteApply) return;
    const key = String(scene.id);
    onRegisterRemoteApply(key, sync.applyRemoteOps);
    return () => onRegisterRemoteApply(key, null);
  }, [scene.id, sync.applyRemoteOps, onRegisterRemoteApply]);

  // A remote op landed for this scene while it was windowed out — the snapshot
  // we mounted from may be stale, so refetch once and clear the flag (C2 / M1).
  useEffect(() => {
    if (!remoteStale) return;
    sync.reconcile();
    onRemoteStaleHandled?.(String(scene.id)); // string key — matches droppedScenes
  }, [remoteStale, scene.id, sync.reconcile, onRemoteStaleHandled]);

  // TipTap mode (M1): route every `sync.elements` change (remote splice,
  // reconcile, conflict resolution, 409 replay) through the imperative
  // applyExternalElements API. This ALSO fires after our own local edits
  // (dispatchOps → useSceneSync's setElements), but that's harmless —
  // applyExternalElements' own field-wise equality check makes those calls a
  // no-op (the doc already shows exactly what `sync.elements` now says,
  // since the edit originated FROM the editor), which is the loop guard.
  useEffect(() => {
    if (!tiptapOn) return;
    tiptapRef.current?.applyExternalElements(sync.elements);
  }, [tiptapOn, sync.elements]);

  // EmptySceneHint's seed action in TipTap mode: the PM schema requires
  // `scriptElement+` (at least one node), so an empty scene can never mount
  // a TipTapSceneEditor — seed the first element via the same anchored
  // insert op the legacy path's onEnter(…, {elementId: null}) issues, and
  // the effect above hands the freshly non-empty `sync.elements` to a
  // freshly-mounted editor.
  const handleTiptapSeed = useCallback(() => {
    const id = newElementId();
    const op: ElementOp = { op: 'insert', element_id: id, payload: { type: 'action', text: '' } };
    sync.dispatchOps([op], applyLocal(sync.elements, [op]));
    // M2 carry-over from M1: focus the freshly-seeded element. The editor
    // doesn't exist yet on THIS render (EmptySceneHint→TipTapSceneEditor is
    // a fresh mount triggered by `sync.elements` going non-empty) — rAF
    // runs after React has committed the new mount (same pattern as the
    // legacy machine's `applyResult` focus-the-new-cursor call below).
    requestAnimationFrame(() => tiptapRef.current?.focusElement(id, true));
  }, [sync]);

  // TipTap mode: selection changes report the focused element up exactly
  // like the legacy handleFocus does (toolbar follow / Statistics cursor).
  const handleTiptapFocusCursor = useCallback(
    (elementId: string | null) => {
      setFocusedElementId(elementId);
      onFocusElement?.({ sceneId: scene.id, elementId, field: 'element' });
    },
    [onFocusElement, scene.id],
  );

  // ── TipTap M2: slash menu ('/' at block start) ────────────────────────
  // Mirrors legacy `handleInput`'s slash branch: a fresh '/' opens/updates
  // the picker (position looked up via the SAME `data-el-id` DOM query
  // legacy uses — the NodeViewContent div carries the identical attribute);
  // anything else closes it, but only if IT was the one open.
  const handleTiptapSlashChange = useCallback((elementId: string, query: string | null) => {
    if (query !== null) {
      const node = containerRef.current?.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`);
      const position = node
        ? { top: node.offsetTop + node.offsetHeight, left: node.offsetLeft }
        : undefined;
      setSlashActive(0);
      setSlash({ elementId, query, position });
    } else if (slashRef.current?.elementId === elementId) {
      setSlash(null);
    }
  }, []);

  // Apply a slash pick in TipTap mode: an attrs+text-clear transaction for
  // the immediate visual update (see TipTapSceneEditor's module doc) plus
  // the SAME update op legacy dispatches (data plane unchanged).
  const applySlashTiptap = useCallback(
    (type: ElementType) => {
      const s = slashRef.current;
      if (!s) return;
      const op: ElementOp = { op: 'update', element_id: s.elementId, payload: { type, text: '' } };
      tiptapRef.current?.retypeElement(s.elementId, type, true);
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
      setSlash(null);
    },
    [sync],
  );

  const tiptapSlashMenu: MenuBridge | null = useMemo(() => {
    if (!slash) return null;
    return {
      elementId: slash.elementId,
      onArrowDown: () =>
        setSlashActive((prev) => (slashFilteredRef.current.length > 0 ? (prev + 1) % slashFilteredRef.current.length : prev)),
      onArrowUp: () =>
        setSlashActive((prev) =>
          slashFilteredRef.current.length > 0
            ? (prev - 1 + slashFilteredRef.current.length) % slashFilteredRef.current.length
            : prev,
        ),
      onApply: () => {
        const filtered = slashFilteredRef.current;
        const active = slashActiveRef.current;
        if (filtered.length > 0 && active >= 0 && active < filtered.length) {
          applySlashTiptap(filtered[active].type);
        } else {
          setSlash(null);
        }
      },
      onEscape: () => setSlash(null),
    };
  }, [slash, applySlashTiptap]);

  // ── TipTap M2: mentions + character-cue picker ────────────────────────
  const handleTiptapMentionOpen = useCallback(
    (elementId: string, kind: 'inline' | 'character' | 'transition', query: string) => {
      const node = containerRef.current?.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`);
      const position = node
        ? { top: node.offsetTop + node.offsetHeight, left: node.offsetLeft }
        : undefined;
      setMention({ elementId, kind, query, position });
    },
    [],
  );
  const handleTiptapMentionClose = useCallback(() => setMention(null), []);

  const handleTiptapMentionSelect = useCallback(
    (name: string) => {
      const m = mentionRef.current;
      if (!m) return;
      let newText: string;
      if (m.kind !== 'inline') {
        // A character cue IS the name; a transition IS the preset — either
        // way the picked option replaces the whole line.
        newText = name;
      } else {
        const el = elementsRef.current.find((e) => e.id === m.elementId);
        const text = el?.text ?? '';
        const at = text.lastIndexOf('@');
        newText =
          at >= 0
            ? `${text.slice(0, at)}@${name} ${text.slice(at + 1 + m.query.length)}`
            : `${text}@${name} `;
      }
      const op: ElementOp = { op: 'update', element_id: m.elementId, payload: { text: newText } };
      tiptapRef.current?.replaceElementText(m.elementId, newText);
      // Selecting from the popup's embedded search input leaves focus there —
      // focusElement reclaims the editor (caret at the line end). Its
      // synchronous selection-update may re-open the cue picker, but the
      // setMention(null) below runs after and wins.
      tiptapRef.current?.focusElement(m.elementId);
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
      setMention(null);
    },
    [sync],
  );

  // ── Popup-embedded search input paths (laper cue picker) ───────────────
  // The character-cue popup carries its own input; these mirror the line's
  // keyboard semantics for keystrokes that happen INSIDE that input.
  const refocusMentionLine = useCallback(
    (elementId: string) => {
      if (tiptapOn) {
        tiptapRef.current?.focusElement(elementId);
      } else {
        containerRef.current?.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`)?.focus();
      }
    },
    [tiptapOn],
  );

  const handleMentionQueryChange = useCallback((q: string) => {
    setMention((prev) => (prev ? { ...prev, query: q } : prev));
  }, []);

  // Tab in the popup input: abandon the cue, revert the block to action
  // (same semantics as Tab on the line — spec §3.2 laper behaviour).
  const handleMentionTabAction = useCallback(() => {
    const m = mentionRef.current;
    if (!m) return;
    if (m.kind === 'character') {
      const op: ElementOp = { op: 'update', element_id: m.elementId, payload: { type: 'action' } };
      if (tiptapOn) tiptapRef.current?.retypeElement(m.elementId, 'action');
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
    }
    refocusMentionLine(m.elementId);
    setMention(null);
  }, [sync, tiptapOn, refocusMentionLine]);

  // Escape in the popup input: close and hand focus back to the line. Refocus
  // FIRST — its selection-update may re-open the picker, and the close below
  // must win.
  const handleMentionPopClose = useCallback(() => {
    const m = mentionRef.current;
    if (m) refocusMentionLine(m.elementId);
    setMention(null);
  }, [refocusMentionLine]);

  const tiptapMentionMenu: MenuBridge | null = useMemo(() => {
    if (!mention) return null;
    return {
      elementId: mention.elementId,
      onArrowDown: () =>
        setMentionActive((prev) =>
          mentionFilteredRef.current.length > 0 ? (prev + 1) % mentionFilteredRef.current.length : prev,
        ),
      onArrowUp: () =>
        setMentionActive((prev) =>
          mentionFilteredRef.current.length > 0
            ? (prev - 1 + mentionFilteredRef.current.length) % mentionFilteredRef.current.length
            : prev,
        ),
      onApply: () => {
        const filtered = mentionFilteredRef.current;
        const active = mentionActiveRef.current;
        if (filtered.length > 0 && active >= 0 && active < filtered.length) {
          handleTiptapMentionSelect(filtered[active]);
        } else {
          setMention(null);
        }
      },
      onTab: () => {
        // Transition preset picker: decline the key so Tab falls through to
        // the ScriptKeymap's real type-cycle (see MenuBridge.onTab contract).
        if (mention.kind === 'transition') return false;
        if (mention.kind === 'character') {
          const op: ElementOp = { op: 'update', element_id: mention.elementId, payload: { type: 'action' } };
          tiptapRef.current?.retypeElement(mention.elementId, 'action');
          sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
        }
        setMention(null);
      },
      onEscape: () => setMention(null),
    };
  }, [mention, handleTiptapMentionSelect, sync]);

  // Lift optimistic elements to the shell for live Statistics + rail entities
  // (Task 6 ⑥). STRUCTURAL changes (a block created/deleted/moved/retyped —
  // via Tab, slash menu, toolbar, Enter) report IMMEDIATELY so anything
  // derived from types (the toolbar's active pill) never lags; pure text
  // typing keeps the 1s debounce so a keystroke burst collapses into one
  // update.
  const lastSkeletonRef = useRef('');
  useEffect(() => {
    if (!onElementsChange) return;
    const skeleton = sync.elements.map((e) => `${e.id}:${e.type}`).join('|');
    if (skeleton !== lastSkeletonRef.current) {
      lastSkeletonRef.current = skeleton;
      onElementsChange(scene.id, sync.elements);
      return;
    }
    const id = setTimeout(() => onElementsChange(scene.id, sync.elements), 1000);
    return () => clearTimeout(id);
  }, [sync.elements, scene.id, onElementsChange]);

  const onCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);
  const onCompositionEnd = useCallback(() => {
    composingRef.current = false;
  }, []);

  const commitMeta = useCallback(
    (patch: Partial<SceneMeta>) => {
      setMeta((prev) => ({ ...prev, ...patch }));
      pendingMetaRef.current = { ...(pendingMetaRef.current ?? {}), ...patch };
      if (metaTimerRef.current) clearTimeout(metaTimerRef.current);
      metaTimerRef.current = setTimeout(() => {
        pendingMetaRef.current = null;
        updateSceneMeta(scene.id, {
          heading_int_ext: patch.heading_int_ext ?? undefined,
          location_text: patch.location_text ?? undefined,
          time_of_day: patch.time_of_day ?? undefined,
        }).catch((err) => console.error('[SceneBlock] updateSceneMeta failed', err));
      }, META_DEBOUNCE_MS);
    },
    [scene.id],
  );

  const enterHeadingEdit = useCallback(() => setHeadingEditing(true), []);

  // Leaving the head row entirely (focus moved outside it) returns to read mode.
  const handleHeadRowBlur = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    // A window/tab switch blurs the control without leaving the row — keep the
    // edit state so the writer returns to the same selects, not a collapsed slug.
    if (!document.hasFocus()) return;
    const next = e.relatedTarget as Node | null;
    if (next && headRowRef.current?.contains(next)) return;
    setHeadingEditing(false);
  }, []);

  // Esc abandons heading editing and returns focus to the read-mode slug.
  const handleHeadRowKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Escape') return;
    e.stopPropagation();
    setHeadingEditing(false);
    requestAnimationFrame(() => headingDisplayRef.current?.focus());
  }, []);

  const displayHeading = formatSceneHeading(meta, format);

  const LayoutEngine = format === 'asian' ? AsianLayout : HollywoodLayout;

  // The focused line IS the ARIA combobox when a picker is open — feed the layout
  // engine the listbox id + active option id so it wires them onto that line.
  const lineMention: LineMention | null = mention
    ? {
        elementId: mention.elementId,
        listboxId: mentionListId,
        // No matches → collapsed combobox: no active option, aria-expanded=false
        // (Task 6 ③ — drop the dangling activedescendant / controls refs).
        expanded: mentionFiltered.length > 0,
        activeOptionId:
          mentionFiltered.length > 0 ? `${mentionListId}-opt-${mentionActive}` : undefined,
      }
    : null;

  // ── Reorder wiring (Task 10) ──────────────────────────────────────────────
  const isDragging = reorder?.draggingId === scene.id;
  const dropEdge =
    reorder?.dropTarget && reorder.dropTarget.sceneId === scene.id
      ? reorder.dropTarget.edge
      : null;

  const handleHandleKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!reorder || !e.altKey) return;
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      reorder.onKeyboardMove(scene.id, 'up');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      reorder.onKeyboardMove(scene.id, 'down');
    }
  };

  const handleBlockDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!reorder || !reorder.draggingId || reorder.draggingId === scene.id) return;
    // preventDefault marks this a valid drop target (HTML5 DnD contract).
    e.preventDefault();
    reorder.onDragOver(scene.id, edgeFromPointer(e.currentTarget, e.clientY));
  };
  const handleBlockDrop = (e: DragEvent<HTMLDivElement>) => {
    if (!reorder || !reorder.draggingId) return;
    e.preventDefault();
    reorder.onDrop(scene.id, edgeFromPointer(e.currentTarget, e.clientY));
  };

  // ── Element reorder wiring (hover-gutter handle) ──────────────────────────
  // Mirrors the scene-level pattern above but drives a `move` op WITHIN this
  // scene. Edge 'top' lands the dragged element BEFORE the target (before_id),
  // 'bottom' lands it AFTER (after_id); applyLocal re-anchors, and useSceneSync
  // dispatches the op optimistically (route-C: no direct phase writes).
  //
  // CROSS-SCENE: a drag whose source is ANOTHER scene (shell-broadcast via the
  // `elementDrag` prop) is accepted too — the drop inserts the element here
  // (insert op, full payload, same id) and asks the shell to delete it from
  // the source scene. Insert-then-delete order so a failure can only duplicate,
  // never lose, the paragraph.
  const externalDrag = elementDrag && elementDrag.sceneId !== scene.id ? elementDrag : null;

  // Register a dispatcher the shell can use for the cross-scene delete leg.
  useEffect(() => {
    if (!onRegisterExternalOps) return;
    onRegisterExternalOps(scene.id, (ops) => {
      syncRef.current.dispatchOps(ops, applyLocal(elementsRef.current, ops));
    });
    return () => onRegisterExternalOps(scene.id, null);
  }, [scene.id, onRegisterExternalOps]);

  const handleElementDragStart = useCallback(
    (elementId: string) => {
      setDraggingElementId(elementId);
      const el = elementsRef.current.find((e) => e.id === elementId);
      if (el) onElementDragBegin?.(scene.id, el);
    },
    [scene.id, onElementDragBegin],
  );
  const handleElementDragEnd = useCallback(() => {
    setDraggingElementId(null);
    setElementDropTarget(null);
    onElementDragDone?.();
  }, [onElementDragDone]);
  const handleElementDragOver = useCallback((elementId: string, edge: 'top' | 'bottom') => {
    setElementDropTarget({ elementId, edge });
  }, []);
  /** Build the insert op that lands an external element at the given anchor. */
  const acceptExternalDrop = useCallback(
    (anchor: { before_id?: string; after_id?: string }) => {
      if (!externalDrag) return;
      const { sceneId: fromSceneId, element } = externalDrag;
      const op: ElementOp = {
        op: 'insert',
        element_id: element.id,
        payload: {
          type: element.type,
          text: element.text,
          ...(element.character_id ? { character_id: element.character_id } : {}),
        },
        ...anchor,
      };
      sync.dispatchOps([op], applyLocal(sync.elements, [op]));
      onCrossSceneDelete?.(fromSceneId, element.id);
      onElementDragDone?.();
      // TipTap mode (M2 item 6): focus the newly-landed element once the M1
      // applyExternalElements effect (triggered by the `sync.elements`
      // change above) has rebuilt the doc — rAF runs after React commits.
      if (tiptapOn) {
        const droppedId = element.id;
        requestAnimationFrame(() => tiptapRef.current?.focusElement(droppedId));
      }
    },
    [externalDrag, sync, onCrossSceneDelete, onElementDragDone, tiptapOn],
  );
  const handleElementDrop = useCallback(
    (targetId: string, edge: 'top' | 'bottom') => {
      setElementDropTarget(null);
      const dragging = draggingElementId;
      setDraggingElementId(null);
      if (dragging) {
        // Same-scene reorder (existing move-op path). Dropping onto itself is a
        // no-op; applyMove would also self-anchor-skip, but bailing here avoids
        // an empty dispatch + version bump.
        if (dragging === targetId) return;
        const op: ElementOp =
          edge === 'top'
            ? { op: 'move', element_id: dragging, before_id: targetId }
            : { op: 'move', element_id: dragging, after_id: targetId };
        sync.dispatchOps([op], applyLocal(sync.elements, [op]));
        return;
      }
      if (externalDrag) {
        acceptExternalDrop(edge === 'top' ? { before_id: targetId } : { after_id: targetId });
      }
    },
    [draggingElementId, sync, externalDrag, acceptExternalDrop],
  );
  // External drop on the scene HEADING row → insert at the head of this scene
  // (covers empty scenes, which have no element rows to target).
  const handleHeadRowExternalDrop = useCallback(() => {
    if (!externalDrag) return;
    const firstId = elementsRef.current[0]?.id;
    acceptExternalDrop(firstId ? { before_id: firstId } : {});
  }, [externalDrag, acceptExternalDrop]);

  // ── Copilot summon (Task 11) ──────────────────────────────────────────────
  const clearCopilot = useCallback(() => {
    setCopilotSelection([]);
    setCopilotAnchor(null);
    setCopilotEdits(null);
    setCopilotInverse(null);
    setCopilotStatus(null);
    setCopilotInstruction('');
    setCopilotRequest('idle');
    setCopilotProposal(null);
    setCopilotSummary(null);
    setCopilotFailedDetail(null);
    // copilotDisabled is intentionally NOT reset: the 404 flag-off memory
    // persists for the session so we don't re-probe a known-off endpoint.
  }, []);

  const handleTickClick = useCallback(
    (elementId: string, shiftKey: boolean) => {
      // Any selection change starts a fresh turn (drop last turn's undo/edits).
      setCopilotEdits(null);
      setCopilotInverse(null);
      setCopilotSummary(null);
      setCopilotProposal(null);
      setCopilotFailedDetail(null);
      setCopilotStatus(null);
      const els = elementsRef.current;
      if (shiftKey && copilotAnchor) {
        const a = els.findIndex((e) => e.id === copilotAnchor);
        const b = els.findIndex((e) => e.id === elementId);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = a <= b ? [a, b] : [b, a];
          setCopilotSelection(els.slice(lo, hi + 1).map((e) => e.id));
          return;
        }
      }
      // Plain click: single-select, or toggle off if it was the only selection.
      setCopilotSelection((prev) => (prev.length === 1 && prev[0] === elementId ? [] : [elementId]));
      setCopilotAnchor(elementId);
    },
    [copilotAnchor],
  );

  const handlePolish = useCallback(() => {
    const els = elementsRef.current;
    const ops = buildPolishOps(els, copilotSelection);
    if (ops.length === 0) {
      setCopilotEdits(0);
      setCopilotInverse(null);
      setCopilotStatus('done');
      return;
    }
    const inverse = buildInverse(ops, els);
    sync.dispatchOps(ops, applyLocal(els, ops));
    setCopilotEdits(ops.length);
    setCopilotInverse(inverse);
    setCopilotStatus('applying');
  }, [copilotSelection, sync]);

  const handleCopilotUndo = useCallback(() => {
    const inverse = copilotInverse;
    if (!inverse || inverse.length === 0) return;
    sync.dispatchOps(inverse, applyLocal(elementsRef.current, inverse));
    setCopilotEdits(null);
    setCopilotInverse(null);
    setCopilotStatus(null);
    setCopilotSummary(null);
  }, [copilotInverse, sync]);

  // Apply reconciled ops the same way Polish does: store the inverse for Undo,
  // dispatch through the scene's op queue, and hand the phase to the shared
  // saveState-driven copilotStatus so it settles applying → done.
  const applyCopilotOps = useCallback(
    (ops: ElementOp[], summary: string) => {
      const els = elementsRef.current;
      const inverse = buildInverse(ops, els);
      // If-Match uses sync's CURRENT version; the ops were generated against
      // base_version. If a local edit advanced the version since the request
      // started, dispatch will 409 and flow through the existing conflict path
      // (acceptable — zero new concurrency surface, spec §2.2).
      sync.dispatchOps(ops, applyLocal(els, ops));
      setCopilotEdits(ops.length);
      setCopilotInverse(inverse);
      setCopilotSummary(summary);
      setCopilotProposal(null);
      setCopilotInstruction('');
      setCopilotRequest('idle');
      setCopilotStatus('applying');
    },
    [sync],
  );

  const handleCopilotSubmit = useCallback(() => {
    const instruction = copilotInstruction.trim();
    if (!instruction || copilotDisabled) return;
    // Fresh turn: drop any prior result/undo/proposal/error.
    setCopilotEdits(null);
    setCopilotInverse(null);
    setCopilotSummary(null);
    setCopilotProposal(null);
    setCopilotFailedDetail(null);
    setCopilotStatus(null);
    setCopilotRequest('thinking');
    const readVersion = sync.version;
    requestCopilotOps(scene.id, instruction, readVersion)
      .then((result) => {
        if (result.proposal) {
          // Stale read: do NOT auto-apply — park for review (Apply/Discard).
          setCopilotProposal({ ops: result.ops, summary: result.summary });
          setCopilotRequest('idle');
          return;
        }
        applyCopilotOps(result.ops, result.summary);
      })
      .catch((err) => {
        setCopilotRequest('idle');
        if (err instanceof CopilotDisabledError) {
          // Flag off (404): disable the box for the session — don't re-probe.
          setCopilotDisabled(true);
          return;
        }
        if (err instanceof OpRejectedError) {
          // 422: elements untouched; surface the op code, keep the instruction.
          setCopilotFailedDetail(String(err.code));
          setCopilotStatus('failed');
          return;
        }
        console.error('[SceneBlock] copilot request failed', err);
        setCopilotFailedDetail(null);
        setCopilotStatus('failed');
      });
  }, [copilotInstruction, copilotDisabled, scene.id, sync.version, applyCopilotOps]);

  const handleCopilotApply = useCallback(() => {
    const proposal = copilotProposal;
    if (!proposal) return;
    applyCopilotOps(proposal.ops, proposal.summary);
  }, [copilotProposal, applyCopilotOps]);

  const handleCopilotDiscard = useCallback(() => {
    setCopilotProposal(null);
    setCopilotRequest('idle');
  }, []);

  // Tell the shell which scene owns the card; if another scene takes over, drop
  // our selection so only one card is ever summoned (spec: non-persistent).
  const hasCopilotSelection = copilotSelection.length > 0;
  useEffect(() => {
    if (hasCopilotSelection) onCopilotActivate?.(scene.id);
  }, [hasCopilotSelection, scene.id, onCopilotActivate]);
  useEffect(() => {
    if (
      hasCopilotSelection &&
      copilotActiveSceneId != null &&
      copilotActiveSceneId !== scene.id
    ) {
      clearCopilot();
    }
  }, [copilotActiveSceneId, hasCopilotSelection, scene.id, clearCopilot]);

  const copilotSelectedIds = useMemo(() => new Set(copilotSelection), [copilotSelection]);

  const copilotPhase: CopilotPhase = (() => {
    if (copilotRequest === 'thinking') return 'applying';
    if (copilotProposal) return 'proposal';
    if (copilotStatus === 'failed') return 'failed';
    if (copilotEdits === null || copilotStatus === null) return 'attached';
    if (copilotStatus === 'applying') return 'applying';
    return 'done';
  })();

  return (
    <div
      className={`mh-scene-block${isDragging ? ' dragging' : ''}`}
      ref={containerRef}
      data-testid="scene-block"
      data-scene-id={scene.id}
      onDragOver={reorder ? handleBlockDragOver : undefined}
      onDrop={reorder ? handleBlockDrop : undefined}
    >
      {dropEdge === 'before' && (
        <div className="mh-drop-indicator before" data-testid="drop-indicator" aria-hidden="true" />
      )}
      <div
        className={`mh-scene-headrow${format === 'asian' ? ' asian' : ''}`}
        ref={headRowRef}
        onBlur={headingEditing ? handleHeadRowBlur : undefined}
        onKeyDown={headingEditing ? handleHeadRowKeyDown : undefined}
        // Focus anywhere in the heading row (read-mode slug button, the
        // int/ext + time selects, the location input — focus bubbles) reports
        // a HEADING cursor so the toolbar's active pill switches to Scene.
        onFocus={() =>
          onFocusElement?.({ sceneId: scene.id, elementId: null, field: 'heading_int_ext' })
        }
        // Cross-scene drop target: dropping a dragged paragraph on the heading
        // row lands it at the HEAD of this scene (works for empty scenes too).
        onDragOver={externalDrag ? (e) => e.preventDefault() : undefined}
        onDrop={
          externalDrag
            ? (e) => {
                e.preventDefault();
                e.stopPropagation();
                handleHeadRowExternalDrop();
              }
            : undefined
        }
      >
        {/* IN-FLOW gutter (structural fix for the misalignment class): the
         *  number + drag handle live INSIDE the heading row's flex line, pulled
         *  into the left margin with a negative margin — they share the text's
         *  line box, so they can never drift vertically (the absolute+magic-top
         *  approach misaligned per font/row kind). Same pattern as .mh-el-gutter. */}
        <span className="mh-scene-gutter" contentEditable={false}>
          {/* A1: continuous document-order block number (scene heading = block
           *  `blockIndexBase + 1`), NOT the scene's position among scenes. */}
          <span className="mh-scene-num-badge">{blockIndexBase + 1}</span>
          <button
            type="button"
            className={`mh-drag-handle${isDragging ? ' dragging' : ''}`}
            aria-label={t('editor.dragScene')}
            // Disambiguates the two drag scopes: this handle (on the heading
            // row) moves the WHOLE scene; a paragraph's handle moves that
            // paragraph.
            title="Move scene"
            aria-grabbed={reorder ? isDragging : undefined}
            draggable={!!reorder}
            tabIndex={reorder ? 0 : -1}
            onDragStart={
              reorder
                ? (e) => {
                    e.dataTransfer.effectAllowed = 'move';
                    e.dataTransfer.setData('text/plain', scene.id);
                    reorder.onDragStart(scene.id);
                  }
                : undefined
            }
            onDragEnd={reorder ? () => reorder.onDragEnd() : undefined}
            onKeyDown={handleHandleKeyDown}
          >
            {/* 4-dot (2×2) grid — identical affordance to `.mh-el-drag`. */}
            {[0, 1, 2, 3].map((d) => (
              <span key={d} className="mh-el-dot" aria-hidden="true" />
            ))}
          </button>
        </span>
        {headingEditing ? (
          <>
            <UiSelect
              autoFocus
              triggerClassName="mh-scene-select"
              aria-label={t('editor.intExt')}
              value={meta.heading_int_ext}
              onChange={(e) => commitMeta({ heading_int_ext: e.target.value })}
            >
              <option value="">—</option>
              {INT_EXT_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </UiSelect>
            <input
              className="mh-scene-loc-input"
              aria-label={t('editor.location')}
              value={meta.location_text}
              placeholder={t('editor.locationPlaceholder')}
              onChange={(e) => commitMeta({ location_text: e.target.value })}
            />
            <UiSelect
              triggerClassName="mh-scene-select"
              aria-label={t('editor.timeOfDay')}
              value={meta.time_of_day}
              onChange={(e) => commitMeta({ time_of_day: e.target.value })}
            >
              <option value="">—</option>
              {TIME_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </UiSelect>
          </>
        ) : (
          <button
            type="button"
            ref={headingDisplayRef}
            className={`mh-scene-heading-display${format === 'asian' ? ' asian' : ''}`}
            aria-label={t('editor.editSceneHeading')}
            onClick={enterHeadingEdit}
          >
            {displayHeading || (
              // laper-parity: an unset heading renders as the slug's chip
              // tokens (INT/EXT LOCATION - DAY/NIGHT), not a prose placeholder.
              // Screplay tokens are English by convention; the button's
              // aria-label above still announces the editable purpose.
              <span className="mh-scene-heading-empty">
                <span className="mh-heading-chip">INT/EXT</span>{' '}
                <span className="mh-heading-chip">LOCATION</span>
                {' - '}
                <span className="mh-heading-chip">DAY/NIGHT</span>
              </span>
            )}
          </button>
        )}
        <ScenePresenceBadge users={focusPresence ?? []} />
      </div>

      {tiptapOn ? (
        // TipTap surface (M1 sync core + M2 block UX, flag-on): the PM
        // schema requires at least one node, so an empty scene renders
        // EmptySceneHint instead of mounting the editor — same nudge as the
        // legacy path, wired to an anchored insert op instead of the
        // machine's onEnter. Drag/tick/slash/mention wiring below reuses
        // the SAME callbacks/state the legacy engines use (see
        // TipTapSceneEditor's module doc) — one behavior, two surfaces.
        sync.elements.length === 0 ? (
          <EmptySceneHint onSeed={handleTiptapSeed} />
        ) : (
          <TipTapSceneEditor
            // M3: format is a mount-time snapshot (see TipTapSceneEditor's
            // module doc, "format switch recreates the editor") — keying on
            // it forces a full remount (flushing any pending debounce first)
            // whenever the script-wide Hollywood/Asian toggle flips, instead
            // of trying to live-patch the NodeView's structural DOM change.
            key={format}
            ref={tiptapRef}
            initialElements={sync.elements}
            format={format}
            blockIndexBase={blockIndexBase}
            dispatchOps={sync.dispatchOps}
            onFocusCursor={handleTiptapFocusCursor}
            onTickClick={handleTickClick}
            selectedElementIds={copilotSelectedIds}
            draggingElementId={draggingElementId ?? externalDrag?.element.id ?? null}
            dropElementEdge={elementDropTarget}
            onElementDragStart={handleElementDragStart}
            onElementDragOver={handleElementDragOver}
            onElementDrop={handleElementDrop}
            onElementDragEnd={handleElementDragEnd}
            onSlashChange={handleTiptapSlashChange}
            slashMenu={tiptapSlashMenu}
            onMentionOpen={handleTiptapMentionOpen}
            onMentionClose={handleTiptapMentionClose}
            mentionMenu={tiptapMentionMenu}
            pageSeams={pageSeams}
            mentionCandidates={mentionCandidates}
          />
        )
      ) : (
        <>
          <MentionNamesContext.Provider value={mentionCandidates}>
            <LayoutEngine
              elements={sync.elements}
              blockIndexBase={blockIndexBase}
              pageSeams={pageSeams}
              focusedElementId={focusedElementId}
              mention={lineMention}
              selectedIds={copilotSelectedIds}
              onTickClick={handleTickClick}
              elementReorder={{
                // An external (cross-scene) drag arms this scene's rows as drop
                // targets exactly like a local drag would — the rows only gate on
                // a non-null dragging id.
                draggingElementId: draggingElementId ?? externalDrag?.element.id ?? null,
                dropTarget: elementDropTarget,
                onDragStart: handleElementDragStart,
                onDragOver: handleElementDragOver,
                onDrop: handleElementDrop,
                onDragEnd: handleElementDragEnd,
              }}
              handlers={{
                onInput: handleInput,
                onKeyDown: handleKeyDown,
                onFocus: handleFocus,
                onPaste: handlePaste,
                onCompositionStart,
                onCompositionEnd,
              }}
            />
          </MentionNamesContext.Provider>

          {sync.elements.length === 0 && (
            <EmptySceneHint
              onSeed={() =>
                applyResult(
                  onEnter(sync.elements, { sceneId: scene.id, elementId: null, field: 'element' }),
                )
              }
            />
          )}
        </>
      )}

      {mention && (
        <MentionCombobox
          candidates={activeMentionCandidates}
          query={mention.query}
          listboxId={mentionListId}
          activeIndex={mentionActive}
          position={mention.position}
          // Mouse-click selection must route through the SAME apply path as
          // keyboard Enter for this mode: legacy's `handleMentionSelect`
          // writes the contentEditable DOM node directly, which would
          // corrupt a PM-managed node — TipTap mode uses the transaction-
          // based `handleTiptapMentionSelect` instead.
          onSelect={tiptapOn ? handleTiptapMentionSelect : handleMentionSelect}
          onHover={setMentionActive}
          kind={mention.kind}
          onQueryChange={handleMentionQueryChange}
          onTabAction={handleMentionTabAction}
          onClose={handleMentionPopClose}
        />
      )}

      {slash && (
        <SlashMenu
          items={slashFiltered}
          activeIndex={slashActive}
          listboxId={slashListId}
          position={slash.position}
          onSelect={tiptapOn ? applySlashTiptap : applySlash}
          onHover={setSlashActive}
        />
      )}

      {hasCopilotSelection && (
        <CopilotCard
          sceneNumber={index + 1}
          selectedCount={copilotSelection.length}
          phase={copilotPhase}
          editsThisTurn={copilotEdits}
          canUndo={!!copilotInverse && copilotInverse.length > 0}
          onPolish={handlePolish}
          onUndo={handleCopilotUndo}
          instruction={copilotInstruction}
          onInstructionChange={setCopilotInstruction}
          onSubmit={handleCopilotSubmit}
          freeTextDisabled={copilotDisabled}
          summary={copilotSummary}
          failedDetail={copilotFailedDetail}
          onApply={handleCopilotApply}
          onDiscard={handleCopilotDiscard}
        />
      )}

      {dropEdge === 'after' && (
        <div className="mh-drop-indicator after" data-testid="drop-indicator" aria-hidden="true" />
      )}
    </div>
  );
}
