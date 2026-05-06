/**
 * B — Chat attachment picker.
 *
 * File picker + upload + preview chips. Used above ChatInput in
 * AIChatPanel. Manages its own staging state — the parent receives
 * the resolved AttachmentRequest[] via onChange.
 *
 * Files are uploaded eagerly on pick (24h TTL on server). Removing a
 * chip drops it from the staged list (no server-side cleanup — the TTL
 * sweeper handles that).
 */
import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Paperclip, X, Image as ImageIcon, Film, FileText, Loader2 } from 'lucide-react';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from './Toast';
import {
  ACCEPT_ATTR,
  MAX_FILES_AT_ONCE,
  formatBytes as _formatBytes,
  validateFileBatch,
} from './ChatAttachmentPicker.helpers';

export interface StagedAttachment {
  kind: 'image' | 'video' | 'pdf';
  url: string;
  filename: string;
  size_bytes: number;
  mime: string | null;
  /** Local data URL for image previews (we keep it client-side; not sent). */
  preview_data_url?: string;
}

interface ChatAttachmentPickerProps {
  attachments: StagedAttachment[];
  onChange: (next: StagedAttachment[]) => void;
  disabled?: boolean;
}

function _kindIcon(kind: StagedAttachment['kind']): React.ReactNode {
  if (kind === 'image') return <ImageIcon className="w-3 h-3" />;
  if (kind === 'video') return <Film className="w-3 h-3" />;
  return <FileText className="w-3 h-3" />;
}

async function _readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('FileReader failed'));
    reader.readAsDataURL(file);
  });
}

export const ChatAttachmentPicker: React.FC<ChatAttachmentPickerProps> = ({
  attachments,
  onChange,
  disabled = false,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  const handlePick = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const handleFiles = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      const files = Array.from(fileList).slice(0, MAX_FILES_AT_ONCE);

      // Pre-validate sizes
      const err = validateFileBatch(files);
      if (err) {
        addToast(t(err), 'error');
        return;
      }

      setUploading(true);
      try {
        const next = [...attachments];
        for (const file of files) {
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
        // Reset the input so re-picking the same file fires onChange
        if (inputRef.current) inputRef.current.value = '';
      }
    },
    [attachments, onChange, addToast],
  );

  const removeAt = useCallback(
    (idx: number) => {
      onChange(attachments.filter((_, i) => i !== idx));
    },
    [attachments, onChange],
  );

  const isDisabled = disabled || uploading;

  // Empty + no upload → just the picker button (anchored to ChatInput layout)
  if (attachments.length === 0 && !uploading) {
    return (
      <button
        type="button"
        onClick={handlePick}
        disabled={isDisabled}
        title={t('chat.attachments.attachTooltip')}
        className="flex-shrink-0 p-1.5 rounded-md text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <Paperclip className="w-4 h-4" />
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </button>
    );
  }

  // With staged attachments → chip strip + add-more button
  return (
    <div className="flex items-center gap-1 flex-wrap min-h-[28px]">
      {attachments.map((a, idx) => (
        <div
          key={`${a.url}-${idx}`}
          className="group flex items-center gap-1.5 px-2 py-0.5 bg-zinc-800 border border-zinc-700 rounded text-[11px] text-zinc-300"
          title={`${a.filename} · ${_formatBytes(a.size_bytes)}`}
        >
          {a.preview_data_url ? (
            <img
              src={a.preview_data_url}
              alt=""
              className="w-4 h-4 object-cover rounded"
            />
          ) : (
            _kindIcon(a.kind)
          )}
          <span className="truncate max-w-[120px]">{a.filename}</span>
          <button
            type="button"
            onClick={() => removeAt(idx)}
            disabled={isDisabled}
            className="text-zinc-500 hover:text-red-400 transition-colors"
            title={t('chat.attachments.remove')}
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      ))}

      <button
        type="button"
        onClick={handlePick}
        disabled={isDisabled}
        title={t('chat.attachments.attachMore')}
        className="flex-shrink-0 p-1 rounded text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {uploading ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Paperclip className="w-3.5 h-3.5" />
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </button>
    </div>
  );
};

export default ChatAttachmentPicker;
