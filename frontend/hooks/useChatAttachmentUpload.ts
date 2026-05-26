/**
 * Shared upload pipeline for chat composers.
 *
 * Both the explicit ChatAttachmentPicker button AND the composer's
 * paste/drag-drop handlers (sub-plan 3) funnel files through this hook so
 * validation, upload, error toasts, and the image-preview data URL all
 * stay consistent.
 *
 * Behavior matches the prior inline ChatAttachmentPicker.handleFiles —
 * no semantic change. The validation error string is returned raw from
 * validateFileBatch and passed directly to addToast; callers that need
 * i18n should wrap at the call-site (ChatAttachmentPicker uses t(err)).
 */
import { useCallback, useState } from 'react';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from '../components/Toast';
import type { StagedAttachment } from '../components/ChatAttachmentPicker';
import {
  MAX_FILES_AT_ONCE,
  validateFileBatch,
} from '../components/ChatAttachmentPicker.helpers';

interface UseChatAttachmentUploadOpts {
  attachments: StagedAttachment[];
  onChange: (next: StagedAttachment[]) => void;
  /**
   * Optional translation function for validation error strings.
   * ChatAttachmentPicker passes `t` from `useTranslation` so that
   * the i18n behavior is preserved (the raw string from validateFileBatch
   * is an i18n key). Other callers (paste/drag) may omit this.
   */
  translateError?: (key: string) => string;
}

async function _readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('FileReader failed'));
    reader.readAsDataURL(file);
  });
}

export function useChatAttachmentUpload(opts: UseChatAttachmentUploadOpts): {
  handleFiles: (files: FileList | File[] | null) => Promise<void>;
  uploading: boolean;
} {
  const { attachments, onChange, translateError } = opts;
  const { addToast } = useToast();
  const [uploading, setUploading] = useState(false);

  const handleFiles = useCallback(
    async (fileList: FileList | File[] | null): Promise<void> => {
      if (!fileList) return;
      const list = Array.from(fileList).slice(0, MAX_FILES_AT_ONCE);
      if (list.length === 0) return;

      const err = validateFileBatch(list);
      if (err) {
        addToast(translateError ? translateError(err) : err, 'error');
        return;
      }

      setUploading(true);
      try {
        const next = [...attachments];
        for (const file of list) {
          try {
            const uploaded = await aiLibraryService.uploadChatAttachment(file);
            const staged: StagedAttachment = {
              kind: uploaded.kind,
              url: uploaded.url,
              filename: uploaded.filename,
              size_bytes: uploaded.size_bytes,
              mime: uploaded.mime,
            };
            // Best-effort image preview (don't block on it)
            if (uploaded.kind === 'image') {
              try {
                staged.preview_data_url = await _readAsDataUrl(file);
              } catch { /* ignore — preview is cosmetic */ }
            }
            next.push(staged);
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            addToast(`Upload "${file.name}" failed: ${msg}`, 'error');
          }
        }
        onChange(next);
      } finally {
        setUploading(false);
      }
    },
    [attachments, onChange, addToast],
  );

  return { handleFiles, uploading };
}
