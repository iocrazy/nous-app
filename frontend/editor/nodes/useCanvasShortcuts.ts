/**
 * useCanvasShortcuts — keyboard map for the node canvas (Phase B Task 2).
 *
 * Binds a keydown listener to a container element and translates keys into
 * canvas actions: arrow keys nudge the selection (1px, 10px with Shift), `+`/`-`
 * zoom, `f` fits the view, Ctrl/Cmd+A selects all, Escape clears the selection.
 * There is deliberately no Delete binding — scene removal lives in the editor, so
 * the canvas can't destroy content by a stray keypress. Every shortcut is
 * bypassed while a text field is focused so typing never moves nodes.
 */
import { useEffect, useRef, type RefObject } from 'react';

/** Nudge step in flow-space px; Shift switches to the coarse step. */
const NUDGE_STEP = 1;
const NUDGE_STEP_LARGE = 10;

export interface CanvasShortcutHandlers {
  /** Move the selection by (dx, dy) flow-space px. */
  onNudge: (dx: number, dy: number) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitView: () => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
}

/** True when the event originated inside a text-editing field. */
function isTextTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && typeof el.closest === 'function' && !!el.closest('input,textarea,[contenteditable]');
}

/** Pure key → handler dispatch (exported for direct unit reasoning). */
export function handleCanvasKey(e: KeyboardEvent, h: CanvasShortcutHandlers): void {
  if (isTextTarget(e.target)) return;
  const step = e.shiftKey ? NUDGE_STEP_LARGE : NUDGE_STEP;
  const mod = e.metaKey || e.ctrlKey;

  switch (e.key) {
    case 'ArrowUp':
      e.preventDefault();
      h.onNudge(0, -step);
      break;
    case 'ArrowDown':
      e.preventDefault();
      h.onNudge(0, step);
      break;
    case 'ArrowLeft':
      e.preventDefault();
      h.onNudge(-step, 0);
      break;
    case 'ArrowRight':
      e.preventDefault();
      h.onNudge(step, 0);
      break;
    case '+':
    case '=': // unshifted `+` on most layouts
      h.onZoomIn();
      break;
    case '-':
    case '_':
      h.onZoomOut();
      break;
    case 'f':
    case 'F':
      if (!mod) h.onFitView();
      break;
    case 'a':
    case 'A':
      if (mod) {
        e.preventDefault();
        h.onSelectAll();
      }
      break;
    case 'Escape':
      h.onClearSelection();
      break;
    default:
      break;
  }
}

export function useCanvasShortcuts(
  containerRef: RefObject<HTMLElement | null>,
  handlers: CanvasShortcutHandlers,
): void {
  // Keep the latest handlers without re-binding the listener each render.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onKeyDown = (e: KeyboardEvent) => handleCanvasKey(e, handlersRef.current);
    el.addEventListener('keydown', onKeyDown);
    return () => el.removeEventListener('keydown', onKeyDown);
  }, [containerRef]);
}
