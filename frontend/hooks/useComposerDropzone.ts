/**
 * Composer drag-and-drop hook.
 *
 * Returns `rootProps` to spread onto the composer's wrapper element +
 * `isDragActive` for rendering an overlay. Uses a ref-counter to avoid
 * the dragenter/dragleave flicker that happens as the cursor crosses
 * the wrapper's internal elements.
 *
 * Browsers default to "navigate to the file URL" on drop — we always
 * `preventDefault()` even when disabled to avoid that, and skip the
 * `onFiles` callback in disabled mode.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';

interface UseComposerDropzoneOpts {
  onFiles: (files: FileList) => void | Promise<void>;
  disabled?: boolean;
}

interface UseComposerDropzoneResult {
  rootProps: {
    onDragEnter: (e: DragEvent) => void;
    onDragOver: (e: DragEvent) => void;
    onDragLeave: (e: DragEvent) => void;
    onDrop: (e: DragEvent) => void;
  };
  isDragActive: boolean;
}

export function useComposerDropzone(
  opts: UseComposerDropzoneOpts,
): UseComposerDropzoneResult {
  const { onFiles, disabled = false } = opts;
  const [isDragActive, setIsDragActive] = useState(false);
  // Counter approach: dragenter/leave fire for every child transition.
  // Treat active as counter > 0 so the overlay doesn't flicker when the
  // user crosses internal elements.
  const counter = useRef(0);

  const onDragEnter = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (disabled) return;
      counter.current += 1;
      if (counter.current === 1) setIsDragActive(true);
    },
    [disabled],
  );

  const onDragOver = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (disabled) return;
      // Tell the browser this is a copy-style drop (cursor + ghost).
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    },
    [disabled],
  );

  const onDragLeave = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      // Always advance the counter on leave — if we skipped it when disabled,
      // a flip from enabled→disabled mid-drag would leave the counter stuck
      // above 0 and the overlay would never clear.
      counter.current = Math.max(0, counter.current - 1);
      if (counter.current === 0) setIsDragActive(false);
    },
    [],
  );

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      // Always reset state on drop regardless of disabled.
      counter.current = 0;
      setIsDragActive(false);
      if (disabled) return;
      const files = e.dataTransfer?.files;
      if (!files || files.length === 0) return;
      void onFiles(files);
    },
    [disabled, onFiles],
  );

  // If `disabled` flips true mid-drag (e.g. an upload starts and the parent
  // wants to block further drops), clear the overlay immediately. Without
  // this the user sees a "Drop files to attach" overlay that no longer
  // accepts drops.
  useEffect(() => {
    if (disabled) {
      counter.current = 0;
      setIsDragActive(false);
    }
  }, [disabled]);

  return {
    rootProps: { onDragEnter, onDragOver, onDragLeave, onDrop },
    isDragActive,
  };
}
