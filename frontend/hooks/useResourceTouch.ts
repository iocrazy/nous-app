// frontend/hooks/useResourceTouch.ts

/**
 * Touch interaction handlers for ResourcesViewInner.
 * Covers: drag-and-drop via touch, long-press context menu,
 * double-tap detection, and empty-area touch context menu.
 */

import React, { useRef, useCallback } from 'react';
import { useTouchDragDrop } from './useTouchDragDrop';

interface UseResourceTouchOptions {
  onDrop: (targetFolderId: string | null, droppedIds: string[]) => Promise<void>;
  selectedIds: Set<string>;
  isResourcesView: boolean;
  onFileContextMenu: (e: React.MouseEvent, item: any) => void;
  onFolderContextMenu: (e: React.MouseEvent, folder: any) => void;
  onEmptyAreaContextMenu: (e: React.MouseEvent) => void;
}

export function useResourceTouch({
  onDrop,
  selectedIds,
  isResourcesView,
  onFileContextMenu,
  onFolderContextMenu,
  onEmptyAreaContextMenu,
}: UseResourceTouchOptions) {
  // ─── Touch drag-and-drop ──────────────────────────────
  const {
    dragState: touchDragState,
    startDrag: startTouchDrag,
    handleTouchMove: handleTouchDragMove,
    handleTouchEnd: handleTouchDragEnd,
    isDropTarget: isTouchDropTarget,
  } = useTouchDragDrop({ onDrop, selectedIds });

  // ─── Double-tap detection ─────────────────────────────
  const DOUBLE_TAP_DELAY = 300;
  const lastTapRef = useRef<{ id: string; time: number } | null>(null);

  const handleTap = useCallback((id: string, onDoubleTap: () => void) => {
    const now = Date.now();
    if (lastTapRef.current && lastTapRef.current.id === id && now - lastTapRef.current.time < DOUBLE_TAP_DELAY) {
      lastTapRef.current = null;
      onDoubleTap();
    } else {
      lastTapRef.current = { id, time: now };
    }
  }, []);

  // ─── Long-press / touch handlers per item ─────────────
  const getItemTouchHandlers = useCallback(
    (itemType: 'file' | 'folder', item: any) => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      let startPos: { x: number; y: number } | null = null;
      let dragStarted = false;
      return {
        onTouchStart: (e: React.TouchEvent) => {
          const touch = e.touches[0];
          startPos = { x: touch.clientX, y: touch.clientY };
          dragStarted = false;
          timer = setTimeout(() => {
            const mockEvent = {
              preventDefault: () => {},
              stopPropagation: () => {},
              clientX: touch.clientX,
              clientY: touch.clientY,
            } as unknown as React.MouseEvent;
            if (itemType === 'file') onFileContextMenu(mockEvent, item);
            else onFolderContextMenu(mockEvent, item);
            timer = null;
          }, 500);
        },
        onTouchMove: (e: React.TouchEvent) => {
          if (!startPos || !timer || dragStarted) return;
          const touch = e.touches[0];
          const dx = touch.clientX - startPos.x;
          const dy = touch.clientY - startPos.y;
          if (Math.sqrt(dx * dx + dy * dy) > 10) {
            clearTimeout(timer); timer = null; dragStarted = true;
            startTouchDrag(itemType === 'folder' ? `folder:${item.id}` : `item:${item.id}`, e);
          }
        },
        onTouchEnd: () => { if (timer) { clearTimeout(timer); timer = null; } startPos = null; dragStarted = false; },
        onTouchCancel: () => { if (timer) { clearTimeout(timer); timer = null; } startPos = null; dragStarted = false; },
      };
    },
    [onFileContextMenu, onFolderContextMenu, startTouchDrag],
  );

  // ─── Empty area touch handlers ────────────────────────
  const emptyAreaTouchRef = useRef<{
    timer: ReturnType<typeof setTimeout> | null;
    startPos: { x: number; y: number } | null;
  }>({ timer: null, startPos: null });

  const handleEmptyAreaTouchStart = useCallback((e: React.TouchEvent) => {
    if (!isResourcesView) return;
    const target = e.target as HTMLElement;
    if (target.closest('[data-context-item]')) return;
    const touch = e.touches[0];
    emptyAreaTouchRef.current.startPos = { x: touch.clientX, y: touch.clientY };
    emptyAreaTouchRef.current.timer = setTimeout(() => {
      onEmptyAreaContextMenu({
        preventDefault: () => {},
        clientX: touch.clientX,
        clientY: touch.clientY,
        target,
      } as unknown as React.MouseEvent);
    }, 500);
  }, [isResourcesView, onEmptyAreaContextMenu]);

  const handleEmptyAreaTouchMove = useCallback((e: React.TouchEvent) => {
    const ref = emptyAreaTouchRef.current;
    if (!ref.timer || !ref.startPos) return;
    const touch = e.touches[0];
    if (Math.sqrt((touch.clientX - ref.startPos.x) ** 2 + (touch.clientY - ref.startPos.y) ** 2) > 10) {
      clearTimeout(ref.timer); ref.timer = null;
    }
  }, []);

  const handleEmptyAreaTouchEnd = useCallback(() => {
    const ref = emptyAreaTouchRef.current;
    if (ref.timer) { clearTimeout(ref.timer); ref.timer = null; }
    ref.startPos = null;
  }, []);

  return {
    touchDragState,
    handleTouchDragMove,
    handleTouchDragEnd,
    isTouchDropTarget,
    handleTap,
    getItemTouchHandlers,
    handleEmptyAreaTouchStart,
    handleEmptyAreaTouchMove,
    handleEmptyAreaTouchEnd,
  };
}
