/**
 * Panel width persistence — `nous.panelWidth.<key>` localStorage convention.
 *
 * Covers: init reads + clamps the stored value; a garbage/out-of-range value
 * falls back to `initial`; omitting `storageKey` leaves `useResizablePanel`
 * behaviorally identical to before; and the write only happens once, on
 * mouseup, never during mousemove (drag-heavy resize must not hammer
 * localStorage).
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  useResizablePanel,
  loadPanelWidth,
  savePanelWidth,
  MIN_PANEL_WIDTH,
  MAX_PANEL_WIDTH,
  DEFAULT_PANEL_WIDTH,
} from './DetailCardKit';

function fireMouseEvent(type: 'mousemove' | 'mouseup', clientX: number) {
  window.dispatchEvent(new MouseEvent(type, { clientX }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('loadPanelWidth', () => {
  it('returns the stored value clamped into [min, max]', () => {
    window.localStorage.setItem('nous.panelWidth.resource-detail', '500');
    expect(loadPanelWidth('resource-detail', DEFAULT_PANEL_WIDTH)).toBe(500);
  });

  it('clamps a stored value above max down to max', () => {
    window.localStorage.setItem('nous.panelWidth.resource-detail', String(MAX_PANEL_WIDTH + 400));
    expect(loadPanelWidth('resource-detail', DEFAULT_PANEL_WIDTH)).toBe(MAX_PANEL_WIDTH);
  });

  it('clamps a stored value below min up to min', () => {
    window.localStorage.setItem('nous.panelWidth.resource-detail', '1');
    expect(loadPanelWidth('resource-detail', DEFAULT_PANEL_WIDTH)).toBe(MIN_PANEL_WIDTH);
  });

  it('falls back to initial on a garbage (non-numeric) stored value', () => {
    window.localStorage.setItem('nous.panelWidth.resource-detail', 'not-a-number');
    expect(loadPanelWidth('resource-detail', DEFAULT_PANEL_WIDTH)).toBe(DEFAULT_PANEL_WIDTH);
  });

  it('falls back to initial when nothing is stored', () => {
    expect(loadPanelWidth('never-written', DEFAULT_PANEL_WIDTH)).toBe(DEFAULT_PANEL_WIDTH);
  });

  it('falls back to initial when localStorage throws (privacy mode)', () => {
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('privacy mode');
    });
    expect(loadPanelWidth('resource-detail', DEFAULT_PANEL_WIDTH)).toBe(DEFAULT_PANEL_WIDTH);
  });
});

describe('savePanelWidth', () => {
  it('does not throw when localStorage is unavailable (privacy mode)', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('privacy mode');
    });
    expect(() => savePanelWidth('resource-detail', 500)).not.toThrow();
  });
});

describe('useResizablePanel — no storageKey (unchanged behavior)', () => {
  it('initializes to `initial` and never touches localStorage', () => {
    const setItemSpy = vi.spyOn(window.localStorage, 'setItem');
    const { result } = renderHook(() => useResizablePanel(450));
    expect(result.current.panelWidth).toBe(450);

    act(() => {
      result.current.handleResizeStart({
        preventDefault: () => {},
        clientX: 500,
      } as React.MouseEvent);
    });
    act(() => fireMouseEvent('mousemove', 400));
    act(() => fireMouseEvent('mouseup', 400));

    expect(setItemSpy).not.toHaveBeenCalled();
  });
});

describe('useResizablePanel — with storageKey', () => {
  it('reads and clamps the stored width on init', () => {
    window.localStorage.setItem('nous.panelWidth.resource-detail', '600');
    const { result } = renderHook(() => useResizablePanel(DEFAULT_PANEL_WIDTH, 'resource-detail'));
    expect(result.current.panelWidth).toBe(600);
  });

  it('falls back to `initial` when the stored value is garbage', () => {
    window.localStorage.setItem('nous.panelWidth.resource-detail', 'garbage');
    const { result } = renderHook(() => useResizablePanel(DEFAULT_PANEL_WIDTH, 'resource-detail'));
    expect(result.current.panelWidth).toBe(DEFAULT_PANEL_WIDTH);
  });

  it('writes exactly once, on mouseup, not on every mousemove', () => {
    const setItemSpy = vi.spyOn(window.localStorage, 'setItem');
    const { result } = renderHook(() => useResizablePanel(DEFAULT_PANEL_WIDTH, 'resource-detail'));

    act(() => {
      result.current.handleResizeStart({
        preventDefault: () => {},
        clientX: 500,
      } as React.MouseEvent);
    });

    // Drag left across several mousemove ticks — panel widens each step.
    act(() => fireMouseEvent('mousemove', 480));
    act(() => fireMouseEvent('mousemove', 460));
    act(() => fireMouseEvent('mousemove', 440));
    expect(setItemSpy).not.toHaveBeenCalled();

    act(() => fireMouseEvent('mouseup', 440));

    expect(setItemSpy).toHaveBeenCalledTimes(1);
    expect(setItemSpy).toHaveBeenCalledWith('nous.panelWidth.resource-detail', String(result.current.panelWidth));
  });

  it('persists the final (post-clamp) width, not the drag start width', () => {
    const { result } = renderHook(() => useResizablePanel(DEFAULT_PANEL_WIDTH, 'resource-detail'));

    act(() => {
      result.current.handleResizeStart({
        preventDefault: () => {},
        clientX: 500,
      } as React.MouseEvent);
    });
    // Drag left by 100px — widens the right-hand panel by 100.
    act(() => fireMouseEvent('mousemove', 400));
    act(() => fireMouseEvent('mouseup', 400));

    expect(result.current.panelWidth).toBe(DEFAULT_PANEL_WIDTH + 100);
    expect(window.localStorage.getItem('nous.panelWidth.resource-detail')).toBe(
      String(DEFAULT_PANEL_WIDTH + 100),
    );
  });
});
