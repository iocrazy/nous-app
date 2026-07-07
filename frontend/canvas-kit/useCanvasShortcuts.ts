/**
 * useCanvasShortcuts — keyboard map for the node canvas (Phase B Task 2).
 *
 * Binds a keydown listener to a container element and translates keys into
 * canvas actions: `+`/`-` zoom, `f` fits the view, Ctrl/Cmd+A selects all,
 * Escape clears the selection. Arrow-key node nudging is deliberately NOT bound
 * here — xyflow's built-in a11y move already handles it (snap-grid step), and a
 * second custom nudge double-counted the movement on real hardware (F1). There
 * is also no Delete binding — scene removal lives in the editor, so the canvas
 * can't destroy content by a stray keypress. Every shortcut is bypassed while a
 * text field is focused so typing never moves nodes.
 */
import { useEffect, useRef, type RefObject } from 'react';

export interface CanvasShortcutHandlers {
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
  const mod = e.metaKey || e.ctrlKey;

  switch (e.key) {
    case '+':
    case '=': // unshifted `+` on most layouts
      // Bare +/= only — Ctrl/Cmd+= is the browser's own zoom, leave it alone.
      if (!mod) h.onZoomIn();
      break;
    case '-':
    case '_':
      if (!mod) h.onZoomOut();
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
