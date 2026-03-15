import { useState, useCallback, useRef } from 'react';

interface DragState {
  isDragging: boolean;
  dragIds: string[];
  dragPosition: { x: number; y: number } | null;
  dropTargetId: string | null;
}

interface UseTouchDragDropOptions {
  onDrop: (dragIds: string[], targetFolderId: string) => void;
  selectedIds: Set<string>;
}

const INITIAL_STATE: DragState = {
  isDragging: false,
  dragIds: [],
  dragPosition: null,
  dropTargetId: null,
};

export function useTouchDragDrop({ onDrop, selectedIds }: UseTouchDragDropOptions) {
  const [dragState, setDragState] = useState<DragState>(INITIAL_STATE);
  const dragStateRef = useRef(INITIAL_STATE);

  const startDrag = useCallback(
    (itemId: string, e: React.TouchEvent) => {
      const touch = e.touches[0];
      const ids = selectedIds.has(itemId)
        ? Array.from(selectedIds)
        : [itemId];

      const newState: DragState = {
        isDragging: true,
        dragIds: ids,
        dragPosition: { x: touch.clientX, y: touch.clientY },
        dropTargetId: null,
      };
      dragStateRef.current = newState;
      setDragState(newState);
    },
    [selectedIds],
  );

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragStateRef.current.isDragging) return;
    e.preventDefault();

    const touch = e.touches[0];
    const { clientX: x, clientY: y } = touch;

    // Detect drop target via data attributes on underlying elements
    const elements = document.elementsFromPoint(x, y);
    let targetId: string | null = null;

    for (const el of elements) {
      const folderEl = (el as HTMLElement).closest('[data-folder-id]');
      if (folderEl) {
        const folderId = folderEl.getAttribute('data-folder-id');
        if (folderId && !dragStateRef.current.dragIds.includes(`folder:${folderId}`)) {
          targetId = folderId;
          break;
        }
      }
    }

    // Also check breadcrumb segments as drop targets
    if (!targetId) {
      for (const el of elements) {
        const breadcrumbEl = (el as HTMLElement).closest('[data-breadcrumb-folder-id]');
        if (breadcrumbEl) {
          const breadcrumbFolderId = breadcrumbEl.getAttribute('data-breadcrumb-folder-id');
          if (breadcrumbFolderId) {
            targetId = breadcrumbFolderId;
            break;
          }
        }
      }
    }

    const newState: DragState = {
      ...dragStateRef.current,
      dragPosition: { x, y },
      dropTargetId: targetId,
    };
    dragStateRef.current = newState;
    setDragState(newState);
  }, []);

  const handleTouchEnd = useCallback(() => {
    const { isDragging, dragIds, dropTargetId } = dragStateRef.current;
    if (isDragging && dropTargetId && dragIds.length > 0) {
      onDrop(dragIds, dropTargetId);
    }
    dragStateRef.current = INITIAL_STATE;
    setDragState(INITIAL_STATE);
  }, [onDrop]);

  const cancelDrag = useCallback(() => {
    dragStateRef.current = INITIAL_STATE;
    setDragState(INITIAL_STATE);
  }, []);

  const isDropTarget = useCallback(
    (folderId: string) => dragState.dropTargetId === folderId,
    [dragState.dropTargetId],
  );

  return {
    dragState,
    startDrag,
    handleTouchMove,
    handleTouchEnd,
    cancelDrag,
    isDropTarget,
  };
}
