import { useEffect, useCallback } from 'react';

interface UseFileKeyboardOptions {
  /** All selectable composite IDs (e.g. "folder:abc", "item:xyz") */
  allSelectableIds: string[];
  /** Currently selected IDs */
  selectedIds: Set<string>;
  /** Set selected IDs */
  setSelectedIds: (ids: Set<string>) => void;
  /** Callback when delete is triggered */
  onDelete?: () => void;
  /** Callback when rename is triggered (single selection) */
  onRename?: (compositeId: string) => void;
  /** Callback when open/enter is triggered (single selection) */
  onOpen?: (compositeId: string) => void;
  /** Callback for new folder */
  onNewFolder?: () => void;
  /** Callback for copy */
  onCopy?: () => void;
  /** Callback for cut */
  onCut?: () => void;
  /** Callback for paste */
  onPaste?: () => void;
  /** Whether the hook is active */
  enabled?: boolean;
}

export function useFileKeyboard({
  allSelectableIds,
  selectedIds,
  setSelectedIds,
  onDelete,
  onRename,
  onOpen,
  onNewFolder,
  onCopy,
  onCut,
  onPaste,
  enabled = true,
}: UseFileKeyboardOptions) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!enabled) return;

      // Skip when focus is on input/textarea/contenteditable
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        return;
      }

      const isMod = e.metaKey || e.ctrlKey;

      // Ctrl/Cmd+A: Select all
      if (isMod && e.key === 'a') {
        e.preventDefault();
        setSelectedIds(new Set(allSelectableIds));
        return;
      }

      // Ctrl/Cmd+C: Copy
      if (isMod && !e.shiftKey && e.key === 'c') {
        e.preventDefault();
        onCopy?.();
        return;
      }

      // Ctrl/Cmd+X: Cut
      if (isMod && e.key === 'x') {
        e.preventDefault();
        onCut?.();
        return;
      }

      // Ctrl/Cmd+V: Paste
      if (isMod && e.key === 'v') {
        e.preventDefault();
        onPaste?.();
        return;
      }

      // Ctrl/Cmd+Shift+N: New folder
      if (isMod && e.shiftKey && e.key === 'N') {
        e.preventDefault();
        onNewFolder?.();
        return;
      }

      // Delete/Backspace: Trash
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedIds.size > 0) {
          e.preventDefault();
          onDelete?.();
        }
        return;
      }

      // F2: Rename (single selection)
      if (e.key === 'F2') {
        if (selectedIds.size === 1) {
          e.preventDefault();
          const id = Array.from(selectedIds)[0];
          onRename?.(id);
        }
        return;
      }

      // Enter: Open (single selection)
      if (e.key === 'Enter') {
        if (selectedIds.size === 1) {
          e.preventDefault();
          const id = Array.from(selectedIds)[0];
          onOpen?.(id);
        }
        return;
      }

      // Escape: Clear selection
      if (e.key === 'Escape') {
        if (selectedIds.size > 0) {
          e.preventDefault();
          setSelectedIds(new Set());
        }
        return;
      }
    },
    [enabled, allSelectableIds, selectedIds, setSelectedIds, onDelete, onRename, onOpen, onNewFolder, onCopy, onCut, onPaste]
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);
}
