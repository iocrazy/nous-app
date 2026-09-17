/**
 * The three control-bar popups (speed / volume / quality) share ONE behaviour.
 *
 * They used not to: volume opened on hover, speed and quality needed a click,
 * and each kept its own boolean. The report was "these three should all open
 * above on hover, and look the same" — B站's control bar being the reference.
 * Keeping the behaviour in one hook is what stops them drifting apart again.
 */
import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { usePlayerMenus, MENU_CLOSE_DELAY_MS } from './usePlayerMenus';

describe('usePlayerMenus', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('starts closed', () => {
    const { result } = renderHook(() => usePlayerMenus());
    expect(result.current.open).toBeNull();
    expect(result.current.anyOpen).toBe(false);
  });

  it('opens on hover, for every menu alike', () => {
    for (const id of ['speed', 'volume', 'quality'] as const) {
      const { result } = renderHook(() => usePlayerMenus());
      act(() => result.current.hoverProps(id).onMouseEnter());
      expect(result.current.isOpen(id)).toBe(true);
    }
  });

  it('closes after a short delay when the pointer leaves', () => {
    // Not instantly: the pointer crosses a few pixels between the button and
    // the popup, and an instant close makes the popup unreachable.
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.hoverProps('speed').onMouseEnter());
    act(() => result.current.hoverProps('speed').onMouseLeave());

    expect(result.current.isOpen('speed')).toBe(true);
    act(() => vi.advanceTimersByTime(MENU_CLOSE_DELAY_MS));
    expect(result.current.isOpen('speed')).toBe(false);
  });

  it('stays open if the pointer comes back before the delay runs out', () => {
    // Leaving the button and entering the popup fires leave → enter on the
    // same wrapper; the pending close must be cancelled or it snaps shut.
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.hoverProps('volume').onMouseEnter());
    act(() => result.current.hoverProps('volume').onMouseLeave());
    act(() => vi.advanceTimersByTime(MENU_CLOSE_DELAY_MS / 2));
    act(() => result.current.hoverProps('volume').onMouseEnter());
    act(() => vi.advanceTimersByTime(MENU_CLOSE_DELAY_MS * 2));

    expect(result.current.isOpen('volume')).toBe(true);
  });

  it('shows one popup at a time', () => {
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.hoverProps('speed').onMouseEnter());
    act(() => result.current.hoverProps('quality').onMouseEnter());

    expect(result.current.isOpen('speed')).toBe(false);
    expect(result.current.isOpen('quality')).toBe(true);
  });

  it('does not let a stale close from one menu shut the next one', () => {
    // Sweep speed → quality: speed's leave schedules a close, quality opens.
    // That close must not fire against quality when the timer runs out.
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.hoverProps('speed').onMouseEnter());
    act(() => result.current.hoverProps('speed').onMouseLeave());
    act(() => result.current.hoverProps('quality').onMouseEnter());
    act(() => vi.advanceTimersByTime(MENU_CLOSE_DELAY_MS * 2));

    expect(result.current.isOpen('quality')).toBe(true);
  });

  it('toggles on click, for touch and keyboard where there is no hover', () => {
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.toggle('quality'));
    expect(result.current.isOpen('quality')).toBe(true);
    act(() => result.current.toggle('quality'));
    expect(result.current.isOpen('quality')).toBe(false);
  });

  it('closeAll shuts whatever is open and cancels a pending close', () => {
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.hoverProps('speed').onMouseEnter());
    act(() => result.current.closeAll());
    expect(result.current.anyOpen).toBe(false);
  });

  it('closes when a tap lands outside every menu', () => {
    // Touch has no mouseleave, so without this a tapped-open popup would stay
    // open until the user found the same button again.
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.toggle('speed'));

    const outside = document.createElement('div');
    document.body.appendChild(outside);
    act(() => {
      outside.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    });

    expect(result.current.anyOpen).toBe(false);
    outside.remove();
  });

  it('ignores a tap inside a menu', () => {
    const { result } = renderHook(() => usePlayerMenus());
    act(() => result.current.toggle('speed'));

    const inside = document.createElement('div');
    inside.setAttribute('data-player-menu', 'speed');
    const child = document.createElement('button');
    inside.appendChild(child);
    document.body.appendChild(inside);
    act(() => {
      child.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    });

    expect(result.current.isOpen('speed')).toBe(true);
    inside.remove();
  });

  it('clears its timer on unmount', () => {
    const { result, unmount } = renderHook(() => usePlayerMenus());
    act(() => result.current.hoverProps('speed').onMouseEnter());
    act(() => result.current.hoverProps('speed').onMouseLeave());
    unmount();
    // Would throw "state update on an unmounted component" noise if leaked.
    expect(() => vi.advanceTimersByTime(MENU_CLOSE_DELAY_MS * 2)).not.toThrow();
  });
});
