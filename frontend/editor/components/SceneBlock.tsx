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
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import {
  onBackspaceAtStart,
  onEnter,
  onShiftTab,
  onTab,
  type CursorState,
  type MachineResult,
} from '../editorMachine';
import { applyLocal } from '../opBuilder';
import { newElementId, updateSceneMeta } from '../sceneService';
import type { ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';
import { useSceneSync } from '../useSceneSync';
import { HollywoodLayout } from '../render/HollywoodLayout';
import { AsianLayout } from '../render/AsianLayout';
import type { EditorFormat } from '../useEditorState';

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
}

export function SceneBlock({
  scene,
  index,
  onFocusElement,
  typeCommand,
  format = 'hollywood',
}: SceneBlockProps) {
  const { t } = useTranslation();
  const sync = useSceneSync(scene);
  const [focusedElementId, setFocusedElementId] = useState<string | null>(null);
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

  elementsRef.current = sync.elements;

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

  const handleKeyDown = useCallback(
    (elementId: string, e: KeyboardEvent<HTMLDivElement>) => {
      // Never intervene mid-IME-composition — let the browser compose.
      if (composingRef.current || e.nativeEvent.isComposing) return;
      const cursor: CursorState = { sceneId: scene.id, elementId, field: 'element' };
      const els = elementsRef.current;

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
    [scene.id, applyResult],
  );

  const handleInput = useCallback(
    (elementId: string, text: string) => {
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
    },
    [onFocusElement, scene.id],
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

  return (
    <div
      className="mh-scene-block"
      ref={containerRef}
      data-testid="scene-block"
      data-scene-id={scene.id}
    >
      <button
        type="button"
        className="mh-drag-handle"
        aria-label={t('editor.dragScene')}
        aria-grabbed="false"
        tabIndex={-1}
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

      <LayoutEngine
        elements={sync.elements}
        focusedElementId={focusedElementId}
        handlers={{
          onInput: handleInput,
          onKeyDown: handleKeyDown,
          onFocus: handleFocus,
          onPaste: handlePaste,
          onCompositionStart,
          onCompositionEnd,
        }}
      />

      {sync.elements.length === 0 && (
        <div className="mh-el-line mh-placeholder-line">{t('editor.emptyScene')}</div>
      )}
    </div>
  );
}
