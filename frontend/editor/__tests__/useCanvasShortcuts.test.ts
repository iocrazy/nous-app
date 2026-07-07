/**
 * useCanvasShortcuts — node-canvas keyboard map (Phase B Task 2).
 *
 * Drives the hook through real DOM keydown events dispatched at a container
 * element, asserting: arrow keys are LEFT to xyflow's built-in a11y node move
 * (no custom nudge — F1), the input-focus bypass (no shortcut fires while
 * typing), Ctrl/Cmd+A select-all with preventDefault, and Escape clearing the
 * selection.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useCanvasShortcuts } from '../../canvas-kit/useCanvasShortcuts';

function makeHandlers() {
  return {
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
  it('does not consume or block arrow keys — xyflow owns node nudging (F1)', () => {
    const h = makeHandlers();
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    // Arrow keys are no longer a custom shortcut: nothing fires, and the event
    // is left un-prevented so xyflow's built-in a11y move can act on it.
    const e = keydown(container, { key: 'ArrowRight' });
    keydown(container, { key: 'ArrowUp', shiftKey: true });
    expect(e.defaultPrevented).toBe(false);
    expect(h.onZoomIn).not.toHaveBeenCalled();
    expect(h.onSelectAll).not.toHaveBeenCalled();
    expect(h.onClearSelection).not.toHaveBeenCalled();
  });

  it('bypasses every shortcut while an input is focused', () => {
    const h = makeHandlers();
    const input = document.createElement('input');
    container.appendChild(input);
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    keydown(input, { key: 'a', ctrlKey: true });
    keydown(input, { key: 'Escape' });

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

  it('leaves Ctrl/Cmd + =/- to the browser zoom (no canvas zoom)', () => {
    const h = makeHandlers();
    renderHook(() => useCanvasShortcuts({ current: container }, h));

    keydown(container, { key: '=', ctrlKey: true });
    keydown(container, { key: '-', metaKey: true });
    expect(h.onZoomIn).not.toHaveBeenCalled();
    expect(h.onZoomOut).not.toHaveBeenCalled();
  });
});
