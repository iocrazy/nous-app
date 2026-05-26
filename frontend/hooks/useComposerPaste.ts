/**
 * Composer paste-from-clipboard hook.
 *
 * Returns an `onPaste` handler to attach to the composer's `<textarea>`.
 * Only intercepts when the clipboard actually contains files (e.g. a
 * screenshot copied to clipboard or "Copy Image"); plain-text paste is
 * left untouched so the textarea behaves normally.
 *
 * When disabled is true and the paste contains files, we still
 * preventDefault — otherwise the file would be dumped into the textarea
 * as a binary string. The onFiles callback is skipped.
 */
import { useCallback } from 'react';
import type { ClipboardEvent } from 'react';

interface UseComposerPasteOpts {
  onFiles: (files: FileList) => void | Promise<void>;
  disabled?: boolean;
}

export function useComposerPaste(opts: UseComposerPasteOpts): {
  onPaste: (e: ClipboardEvent) => void;
} {
  const { onFiles, disabled = false } = opts;

  const onPaste = useCallback(
    (e: ClipboardEvent) => {
      const files = e.clipboardData?.files;
      if (!files || files.length === 0) {
        // Plain-text paste — let it through untouched.
        return;
      }
      // File paste — always preventDefault so the binary blob isn't
      // dropped into the textarea as garbage characters.
      e.preventDefault();
      if (disabled) return;
      void onFiles(files);
    },
    [disabled, onFiles],
  );

  return { onPaste };
}
