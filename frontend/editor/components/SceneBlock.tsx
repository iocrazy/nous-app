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
import { buildPolishOps } from '../copilotService';
import type { ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';
import { useSceneSync } from '../useSceneSync';
import { HollywoodLayout } from '../render/HollywoodLayout';
import { AsianLayout } from '../render/AsianLayout';
import { MentionNamesContext, type LineMention } from '../render/layoutShared';
import { MentionCombobox, filterMentionCandidates } from './MentionCombobox';
import { CopilotCard, type CopilotPhase } from './CopilotCard';
import { EmptySceneHint } from './EmptyStates';
import type { EditorFormat } from '../useEditorState';
import type { SaveState } from '../useSceneSync';

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
  /** Called when Esc leaves an element line so the shell can drop `data-editing`. */
  onExitEditing?: () => void;
  /** The scene that currently owns the copilot card (shell keeps it to one). */
  copilotActiveSceneId?: string | null;
  /** Notifies the shell which scene (if any) now holds a copilot selection. */
  onCopilotActivate?: (sceneId: string | null) => void;
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
}: SceneBlockProps) {
  const { t } = useTranslation();
  const sync = useSceneSync(scene);
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
  const [meta, setMeta] = useState<SceneMeta>({
    heading_int_ext: scene.heading_int_ext ?? '',
    location_text: scene.location_text ?? '',
    time_of_day: scene.time_of_day ?? '',
  });

  const containerRef = useRef<HTMLDivElement | null>(null);
  const composingRef = useRef(false);
  const elementsRef = useRef<ScriptElement[]>(sync.elements);
  const inputTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
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
      Object.values(inputTimers).forEach(clearTimeout);
      if (metaTimerRef.current) clearTimeout(metaTimerRef.current);
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

      // Esc leaves the element line: blur so Tab resumes the page's normal focus
      // order, and tell the shell to drop `data-editing` (spec §3.5 a11y).
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
      if (timers[elementId]) clearTimeout(timers[elementId]);
      timers[elementId] = setTimeout(() => {
        const op: ElementOp = { op: 'update', element_id: elementId, payload: { text } };
        const optimistic = applyLocal(elementsRef.current, [op]);
        sync.dispatchOps([op], optimistic);
        delete timers[elementId];
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

  const onCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);
  const onCompositionEnd = useCallback(() => {
    composingRef.current = false;
  }, []);

  const commitMeta = useCallback(
    (patch: Partial<SceneMeta>) => {
      setMeta((prev) => ({ ...prev, ...patch }));
      if (metaTimerRef.current) clearTimeout(metaTimerRef.current);
      metaTimerRef.current = setTimeout(() => {
        updateSceneMeta(scene.id, {
          heading_int_ext: patch.heading_int_ext ?? undefined,
          location_text: patch.location_text ?? undefined,
          time_of_day: patch.time_of_day ?? undefined,
        }).catch((err) => console.error('[SceneBlock] updateSceneMeta failed', err));
      }, META_DEBOUNCE_MS);
    },
    [scene.id],
  );

  const LayoutEngine = format === 'asian' ? AsianLayout : HollywoodLayout;

  // The focused line IS the ARIA combobox when a picker is open — feed the layout
  // engine the listbox id + active option id so it wires them onto that line.
  const lineMention: LineMention | null = mention
    ? {
        elementId: mention.elementId,
        listboxId: mentionListId,
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

  // ── Copilot summon (Task 11) ──────────────────────────────────────────────
  const clearCopilot = useCallback(() => {
    setCopilotSelection([]);
    setCopilotAnchor(null);
    setCopilotEdits(null);
    setCopilotInverse(null);
  }, []);

  const handleTickClick = useCallback(
    (elementId: string, shiftKey: boolean) => {
      // Any selection change starts a fresh turn (drop last turn's undo/edits).
      setCopilotEdits(null);
      setCopilotInverse(null);
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
      return;
    }
    const inverse = buildInverse(ops, els);
    sync.dispatchOps(ops, applyLocal(els, ops));
    setCopilotEdits(ops.length);
    setCopilotInverse(inverse);
  }, [copilotSelection, sync]);

  const handleCopilotUndo = useCallback(() => {
    const inverse = copilotInverse;
    if (!inverse || inverse.length === 0) return;
    sync.dispatchOps(inverse, applyLocal(elementsRef.current, inverse));
    setCopilotEdits(null);
    setCopilotInverse(null);
  }, [copilotInverse, sync]);

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
    if (copilotEdits === null) return 'attached';
    if (sync.saveState === 'saving') return 'applying';
    if (sync.saveState === 'retrying' || sync.saveState === 'conflict') return 'failed';
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

      <div className={`mh-scene-headrow${format === 'asian' ? ' asian' : ''}`}>
        <span className="mh-scene-num-badge">
          {index + 1}
          {format === 'asian' ? '.' : ''}
        </span>
        <select
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
      </div>

      <MentionNamesContext.Provider value={mentionCandidates}>
        <LayoutEngine
          elements={sync.elements}
          focusedElementId={focusedElementId}
          mention={lineMention}
          selectedIds={copilotSelectedIds}
          onTickClick={handleTickClick}
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

      {sync.elements.length === 0 && <EmptySceneHint />}

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
        />
      )}

      {dropEdge === 'after' && (
        <div className="mh-drop-indicator after" data-testid="drop-indicator" aria-hidden="true" />
      )}
    </div>
  );
}
