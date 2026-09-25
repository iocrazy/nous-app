import type React from 'react';
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useResourceTouch } from './useResourceTouch';

function touchEvent(x: number, y: number): React.TouchEvent {
  return {
    touches: [{ clientX: x, clientY: y }],
    preventDefault: () => {},
  } as unknown as React.TouchEvent;
}

describe('useResourceTouch drop', () => {
  const originalElementsFromPoint = document.elementsFromPoint;

  afterEach(() => {
    document.elementsFromPoint = originalElementsFromPoint;
  });

  it('hands the folder id first and the dragged ids second to onDrop', () => {
    const folder = document.createElement('div');
    folder.setAttribute('data-folder-id', '7');
    document.elementsFromPoint = () => [folder];

    const onDrop = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useResourceTouch({
        onDrop,
        selectedIds: new Set<string>(),
        isResourcesView: true,
        onFileContextMenu: vi.fn(),
        onFolderContextMenu: vi.fn(),
        onEmptyAreaContextMenu: vi.fn(),
      }),
    );

    const handlers = result.current.getItemTouchHandlers('file', { id: '42' });
    act(() => {
      handlers.onTouchStart(touchEvent(0, 0));
      handlers.onTouchMove(touchEvent(30, 0));
    });
    act(() => {
      result.current.handleTouchDragMove(touchEvent(40, 0));
    });
    act(() => {
      result.current.handleTouchDragEnd();
    });

    expect(onDrop).toHaveBeenCalledTimes(1);
    expect(onDrop).toHaveBeenCalledWith('7', ['item:42']);
  });
});
