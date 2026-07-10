// canvas-kit/__tests__/canvasShortcuts.test.ts
// Keyboard dispatch additions (G6): bare `z` toggles the zoom-out overview
// (Infinite's 裸 Z 缩小总览). Text fields still swallow every shortcut.

import { describe, expect, it, vi } from 'vitest';
import { handleCanvasKey, type CanvasShortcutHandlers } from '../useCanvasShortcuts';

function handlers(): CanvasShortcutHandlers & { onToggleOverview: () => void } {
  return {
    onZoomIn: vi.fn(),
    onZoomOut: vi.fn(),
    onFitView: vi.fn(),
    onSelectAll: vi.fn(),
    onClearSelection: vi.fn(),
    onToggleOverview: vi.fn(),
  };
}

function key(k: string, extra: Partial<KeyboardEvent> = {}): KeyboardEvent {
  return { key: k, metaKey: false, ctrlKey: false, target: null, preventDefault: vi.fn(), ...extra } as unknown as KeyboardEvent;
}

describe('handleCanvasKey — overview toggle', () => {
  it('bare z fires onToggleOverview', () => {
    const h = handlers();
    handleCanvasKey(key('z'), h);
    expect(h.onToggleOverview).toHaveBeenCalledTimes(1);
  });

  it('mod+z (undo) is NOT captured', () => {
    const h = handlers();
    handleCanvasKey(key('z', { metaKey: true }), h);
    expect(h.onToggleOverview).not.toHaveBeenCalled();
  });

  it('f still fits the view', () => {
    const h = handlers();
    handleCanvasKey(key('f'), h);
    expect(h.onFitView).toHaveBeenCalledTimes(1);
  });
});
