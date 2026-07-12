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
import { CopilotCard, type CopilotPhase } from './CopilotCard';
import { EmptySceneHint } from './EmptyStates';
import type { EditorFormat } from '../useEditorState';
import type { SaveState } from '../useSceneSync';
import { ScenePresenceBadge } from '../collab/ScenePresenceBadge';
import type { PresenceUser } from '../collab/useScriptPresence';

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

/** An open @-mention / character-cue picker anchored to one element line. */
interface MentionState {
  elementId: string;
  /** 'inline' = typed `@` inside a line; 'character' = a focused character cue. */
  kind: 'inline' | 'character';
  query: string;
  position?: { top: number; left: number };
}

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
}

/** Which half of a block the pointer is over → the drop edge. */
function edgeFromPointer(el: HTMLElement, clientY: number): 'before' | 'after' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}

export function SceneBlock({
  scene,
  index,
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
  const intExtSelectRef = useRef<HTMLSelectElement | null>(null);
  const composingRef = useRef(false);
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
  const mentionFiltered = useMemo(
    () => (mention ? filterMentionCandidates(mentionCandidates, mention.query) : []),
    [mention, mentionCandidates],
  );
  const mentionFilteredRef = useRef<string[]>(mentionFiltered);
  const mentionActiveRef = useRef(0);

  elementsRef.current = sync.elements;
  mentionRef.current = mention;
  mentionFilteredRef.current = mentionFiltered;
  mentionActiveRef.current = mentionActive;

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
      if (m.kind === 'character') {
        // A character cue IS the name — replace the whole line.
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
        if (e.key === 'Tab') {
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
    [scene.id, applyResult, sync, openMention, handleMentionSelect, onExitEditing],
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
    sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
  }, [typeCommand, scene.id, sync]);

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

  // Lift optimistic elements to the shell for live Statistics + rail entities
  // (Task 6 ⑥), debounced 1s so a burst of keystrokes collapses into one update.
  useEffect(() => {
    if (!onElementsChange) return;
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

  // On entering edit mode, land the caret on the first control (INT/EXT).
  useEffect(() => {
    if (headingEditing) intExtSelectRef.current?.focus();
  }, [headingEditing]);

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
  const handleElementDragStart = useCallback((elementId: string) => {
    setDraggingElementId(elementId);
  }, []);
  const handleElementDragEnd = useCallback(() => {
    setDraggingElementId(null);
    setElementDropTarget(null);
  }, []);
  const handleElementDragOver = useCallback((elementId: string, edge: 'top' | 'bottom') => {
    setElementDropTarget({ elementId, edge });
  }, []);
  const handleElementDrop = useCallback(
    (targetId: string, edge: 'top' | 'bottom') => {
      setElementDropTarget(null);
      const dragging = draggingElementId;
      setDraggingElementId(null);
      // Dropping onto itself is a no-op; applyMove would also self-anchor-skip,
      // but bailing here avoids an empty dispatch + version bump.
      if (!dragging || dragging === targetId) return;
      const op: ElementOp =
        edge === 'top'
          ? { op: 'move', element_id: dragging, before_id: targetId }
          : { op: 'move', element_id: dragging, after_id: targetId };
      sync.dispatchOps([op], applyLocal(sync.elements, [op]));
    },
    [draggingElementId, sync],
  );

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
      <button
        type="button"
        className="mh-drag-handle"
        aria-label={t('editor.dragScene')}
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
        ::
      </button>

      <div
        className={`mh-scene-headrow${format === 'asian' ? ' asian' : ''}`}
        ref={headRowRef}
        onBlur={headingEditing ? handleHeadRowBlur : undefined}
        onKeyDown={headingEditing ? handleHeadRowKeyDown : undefined}
      >
        <span className="mh-scene-num-badge">
          {index + 1}
          {format === 'asian' ? '.' : ''}
        </span>
        {headingEditing ? (
          <>
            <select
              ref={intExtSelectRef}
              className="mh-scene-select"
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
            </select>
            <input
              className="mh-scene-loc-input"
              aria-label={t('editor.location')}
              value={meta.location_text}
              placeholder={t('editor.locationPlaceholder')}
              onChange={(e) => commitMeta({ location_text: e.target.value })}
            />
            <select
              className="mh-scene-select"
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
            </select>
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
              <span className="mh-scene-heading-empty">{t('editor.sceneHeadingEmpty')}</span>
            )}
          </button>
        )}
        <ScenePresenceBadge users={focusPresence ?? []} />
      </div>

      <MentionNamesContext.Provider value={mentionCandidates}>
        <LayoutEngine
          elements={sync.elements}
          focusedElementId={focusedElementId}
          mention={lineMention}
          selectedIds={copilotSelectedIds}
          onTickClick={handleTickClick}
          elementReorder={{
            draggingElementId,
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

      {mention && (
        <MentionCombobox
          candidates={mentionCandidates}
          query={mention.query}
          listboxId={mentionListId}
          activeIndex={mentionActive}
          position={mention.position}
          onSelect={handleMentionSelect}
          onHover={setMentionActive}
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
