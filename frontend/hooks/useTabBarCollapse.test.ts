import { renderHook, act } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { useTabBarCollapse } from './useTabBarCollapse';

// A fake scroll surface. The hook reads scrollTop / clientHeight / scrollHeight.
function surface(scrollTop: number, clientHeight: number, scrollHeight: number): HTMLElement {
  return { scrollTop, clientHeight, scrollHeight } as HTMLElement;
}

describe('useTabBarCollapse', () => {
  it('stays expanded at the top edge', () => {
    const { result } = renderHook(() => useTabBarCollapse());
    act(() => result.current.handleScroll(surface(0, 800, 2000)));
    expect(result.current.collapsed).toBe(false);
  });

  it('collapses while scrolling mid-content', () => {
    const { result } = renderHook(() => useTabBarCollapse());
    act(() => result.current.handleScroll(surface(600, 800, 2000)));
    expect(result.current.collapsed).toBe(true);
  });

  it('expands again at the bottom edge', () => {
    const { result } = renderHook(() => useTabBarCollapse());
    // scrollTop + clientHeight === scrollHeight → bottom
    act(() => result.current.handleScroll(surface(1200, 800, 2000)));
    expect(result.current.collapsed).toBe(false);
  });

  it('stays expanded when content does not overflow the viewport', () => {
    const { result } = renderHook(() => useTabBarCollapse());
    act(() => result.current.handleScroll(surface(0, 800, 400)));
    expect(result.current.collapsed).toBe(false);
  });

  it('expand() forces the bar open after a mid-scroll collapse', () => {
    const { result } = renderHook(() => useTabBarCollapse());
    act(() => result.current.handleScroll(surface(600, 800, 2000)));
    expect(result.current.collapsed).toBe(true);
    act(() => result.current.expand());
    expect(result.current.collapsed).toBe(false);
  });
});
