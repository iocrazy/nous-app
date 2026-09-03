// The `L` binding, and the fact that the help panel knows about it.
//
// These two live in different files with NO mechanism keeping them in step:
// `useCanvasShortcuts` is an if/else chain and `canvasShortcuts.ts` is a table
// the `?` panel reads. Nothing enforces they agree, so this file asserts both
// halves of the same fact — which is the whole guard there is.

import { fireEvent, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CANVAS_SHORTCUT_GROUPS } from './canvasShortcuts';
import { useCanvasShortcuts } from './useCanvasShortcuts';

describe('L opens the library', () => {
  it('fires the callback on a bare l', () => {
    const onToggleLibrary = vi.fn();
    renderHook(() => useCanvasShortcuts({ onToggleLibrary }));
    fireEvent.keyDown(window, { key: 'l' });
    expect(onToggleLibrary).toHaveBeenCalledTimes(1);
  });

  it('does not fire while the user is typing', () => {
    const onToggleLibrary = vi.fn();
    renderHook(() => useCanvasShortcuts({ onToggleLibrary }));
    const input = document.createElement('input');
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: 'l' });
    expect(onToggleLibrary).not.toHaveBeenCalled();
    input.remove();
  });

  it('still fires in a read-only session — opening a library changes nothing', () => {
    const onToggleLibrary = vi.fn();
    renderHook(() => useCanvasShortcuts({ onToggleLibrary, readOnly: true }));
    fireEvent.keyDown(window, { key: 'L' });
    expect(onToggleLibrary).toHaveBeenCalled();
  });

  it('the help panel advertises it — the two files have no other link', () => {
    const entries = CANVAS_SHORTCUT_GROUPS.flatMap((g) => g.entries);
    expect(entries.some((e) => e.keys.length === 1 && e.keys[0] === 'L')).toBe(true);
  });
});
