/**
 * useCanvasShortcuts — node-canvas keyboard map (Phase B Task 2).
 *
 * Drives the hook through real DOM keydown events dispatched at a container
 * element, asserting: arrow-key nudge (1px, 10px with Shift) with the default
 * scroll suppressed, the input-focus bypass (no shortcut fires while typing),
 * Ctrl/Cmd+A select-all with preventDefault, and Escape clearing the selection.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useCanvasShortcuts } from '../nodes/useCanvasShortcuts';

function makeHandlers() {
  return {
    onNudge: vi.fn(),
    onZoomIn: vi.fn(),
    onZoomOut: vi.fn(),
    onFitView: vi.fn(),
    onSelectAll: vi.fn(),
    onClearSelection: vi.fn(),
  };
}

let container: HTMLDivElement;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
});

afterEach(() => {
  container.remove();
  vi.clearAllMocks();
});

function keydown(target: Element, init: KeyboardEventInit): KeyboardEvent {
  const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(e);
  return e;
}

describe('useCanvasShortcuts', () => {
  it('nudges the selection 1px on an arrow key and prevents scroll', () => {
    const h = makeHandlers();
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    const e = keydown(container, { key: 'ArrowRight' });
    expect(h.onNudge).toHaveBeenCalledWith(1, 0);
    expect(e.defaultPrevented).toBe(true);
  });

  it('nudges 10px when Shift is held', () => {
    const h = makeHandlers();
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    keydown(container, { key: 'ArrowUp', shiftKey: true });
    expect(h.onNudge).toHaveBeenCalledWith(0, -10);
  });

  it('bypasses every shortcut while an input is focused', () => {
    const h = makeHandlers();
    const input = document.createElement('input');
    container.appendChild(input);
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    keydown(input, { key: 'ArrowRight' });
    keydown(input, { key: 'a', ctrlKey: true });
    keydown(input, { key: 'Escape' });

    expect(h.onNudge).not.toHaveBeenCalled();
    expect(h.onSelectAll).not.toHaveBeenCalled();
    expect(h.onClearSelection).not.toHaveBeenCalled();
  });

  it('selects all on Ctrl/Cmd+A and prevents the browser default', () => {
    const h = makeHandlers();
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    const e = keydown(container, { key: 'a', ctrlKey: true });
    expect(h.onSelectAll).toHaveBeenCalledTimes(1);
    expect(e.defaultPrevented).toBe(true);
  });

  it('clears the selection on Escape', () => {
    const h = makeHandlers();
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    keydown(container, { key: 'Escape' });
    expect(h.onClearSelection).toHaveBeenCalledTimes(1);
  });

  it('maps zoom and fit keys', () => {
    const h = makeHandlers();
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    keydown(container, { key: '+' });
    keydown(container, { key: '-' });
    keydown(container, { key: 'f' });
    expect(h.onZoomIn).toHaveBeenCalledTimes(1);
    expect(h.onZoomOut).toHaveBeenCalledTimes(1);
    expect(h.onFitView).toHaveBeenCalledTimes(1);
  });
});
