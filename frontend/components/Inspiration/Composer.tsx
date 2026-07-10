// Quick-capture box: inline #tag autocomplete, staged multi-format
// attachments (paste / drop / picker), Cmd+Enter submit.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Image as ImageIcon, Mic, Paperclip, X } from 'lucide-react';
import { useToast } from '../Toast';
import {
  createNote,
  uploadAttachment,
  type InspirationNote,
  type NoteAttachment,
  type RefHotspot,
} from '../../services/inspirationService';
import { findActiveTag } from './noteTags';

interface Props {
  onCreated: (note: InspirationNote) => void;
  /** Called when a retried upload finally succeeds, so the page can merge the
   * new attachment into the (already-created) note's card. */
  onAttachmentUploaded?: (noteId: string, attachment: NoteAttachment) => void;
  tagSuggestions: string[];
  prefill?: { content: string; refHotspot?: RefHotspot } | null;
  /** Focus the textarea (caret at start, before any prefilled tag line) on
   * mount — used when a save-as-note prefill just landed. Composer remounts
   * (keyed by prefillNonce) on every new prefill, so this only needs to run
   * once per mount, not react to later changes. */
  autoFocus?: boolean;
}

/** A staged file, tagged with the note it failed to attach to (if any) so a
 * Retry can target the right note without re-creating it. */
interface StagedFile {
  key: string;
  file: File;
  noteId?: string;
}

export const Composer: React.FC<Props> = ({
  onCreated,
  onAttachmentUploaded,
  tagSuggestions,
  prefill,
  autoFocus,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [text, setText] = useState(prefill?.content ?? '');
  const [ref, setRef] = useState(prefill?.refHotspot ?? null);
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [saving, setSaving] = useState(false);
  const [caret, setCaret] = useState(0);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);

  // Mount-level focus: Composer remounts (keyed by prefillNonce) on every new
  // prefill, so this effect naturally runs exactly once per prefill — no
  // dependency-driven re-focus needed.
  useEffect(() => {
    if (!autoFocus) return;
    const ta = taRef.current;
    if (ta) {
      ta.focus();
      ta.setSelectionRange(0, 0);
    }
  }, [autoFocus]);

  const active = useMemo(() => findActiveTag(text, caret), [text, caret]);
  const suggestions = useMemo(() => {
    if (!active) return [];
    return tagSuggestions.filter((s) => s.startsWith(active.prefix) && s !== active.prefix).slice(0, 6);
  }, [active, tagSuggestions]);

  const completeTag = (tag: string) => {
    if (!active) return;
    const next = `${text.slice(0, active.start)}#${tag} `;
    setText(next);
    setCaret(next.length);
    taRef.current?.focus();
  };

  const stageFiles = useCallback((files: FileList | File[]) => {
    setStaged((prev) => [
      ...prev,
      ...Array.from(files).map((file) => ({ key: `f${nextKey.current++}`, file })),
    ]);
  }, []);

  const submit = async () => {
    const content = text.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      const note = await createNote(content, ref ?? undefined);
      const uploaded: NoteAttachment[] = [];
      const failed: StagedFile[] = [];
      for (const item of staged) {
        try {
          uploaded.push(await uploadAttachment(note.id, item.file));
        } catch (err) {
          addToast(
            t('inspiration.uploadFailed', 'Upload failed: {{name}}', { name: item.file.name }) +
              `: ${(err as Error).message}`,
            'error',
          );
          // Keep the file staged (tagged with the note it belongs to) rather
          // than dropping it — the note was already created, so the user
          // only needs to retry the attachment, not the whole note.
          failed.push({ ...item, noteId: note.id });
        }
      }
      onCreated({ ...note, attachments: [...note.attachments, ...uploaded] });
      setText('');
      setStaged(failed);
    } catch (err) {
      addToast((err as Error).message, 'error');
    } finally {
      setSaving(false);
    }
  };

  const retryUpload = async (item: StagedFile) => {
    if (!item.noteId) return;
    try {
      const attachment = await uploadAttachment(item.noteId, item.file);
      setStaged((prev) => prev.filter((s) => s.key !== item.key));
      onAttachmentUploaded?.(item.noteId, attachment);
    } catch (err) {
      addToast(
        t('inspiration.uploadFailed', 'Upload failed: {{name}}', { name: item.file.name }) +
          `: ${(err as Error).message}`,
        'error',
      );
    }
  };

  return (
    <div className="rounded-xl bg-island px-4 pb-3 pt-4">
      {ref && (
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-line border-l-2 border-l-indigo-500 bg-island-2 px-3 py-2">
          <div className="min-w-0 flex-1">
            {ref.source && <span className="text-[10px] font-bold text-content-2">{ref.source}</span>}
            <div className="truncate text-[12px] text-content">{ref.title}</div>
          </div>
          <button
            aria-label="Remove hotspot reference"
            onClick={() => setRef(null)}
            className="shrink-0 text-content-3 hover:text-content"
          >
            <X size={13} />
          </button>
        </div>
      )}
      <textarea
        ref={taRef}
        value={text}
        rows={2}
        placeholder={t('inspiration.placeholder', 'Capture an idea… #tag inline, paste an image, or drop any file')}
        onChange={(e) => {
          setText(e.target.value);
          setCaret(e.target.selectionStart ?? e.target.value.length);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void submit();
          }
        }}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData.files);
          if (files.length) {
            e.preventDefault();
            stageFiles(files);
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files.length) stageFiles(e.dataTransfer.files);
        }}
        onDragOver={(e) => e.preventDefault()}
        className="w-full resize-none bg-transparent text-[13.5px] text-content placeholder:text-content-4 focus:outline-none"
      />

      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-line pt-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => completeTag(s)}
              className="rounded bg-indigo-500/15 px-2 py-0.5 text-xs text-indigo-300 hover:bg-indigo-500/25"
            >
              #{s}
            </button>
          ))}
        </div>
      )}

      {staged.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-2">
          {staged.map((item) => (
            <span
              key={item.key}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs text-content-2 ${
                item.noteId ? 'bg-red-500/10' : 'bg-island-2'
              }`}
            >
              {item.file.name}
              {item.noteId && (
                <button
                  aria-label={`Retry ${item.file.name}`}
                  onClick={() => void retryUpload(item)}
                  className="font-semibold text-indigo-300 hover:text-indigo-200"
                >
                  {t('inspiration.retry', 'Retry')}
                </button>
              )}
              <button
                aria-label={`Remove ${item.file.name}`}
                onClick={() => setStaged((prev) => prev.filter((s) => s.key !== item.key))}
                className="text-content-3 hover:text-content"
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="mt-2 flex items-center gap-1 border-t border-line pt-2">
        <button
          aria-label="Attach image"
          onClick={() => fileRef.current?.click()}
          className="rounded-lg p-1.5 text-content-3 hover:bg-island-2"
        >
          <ImageIcon size={15} />
        </button>
        <button
          aria-label="Attach file"
          onClick={() => fileRef.current?.click()}
          className="rounded-lg p-1.5 text-content-3 hover:bg-island-2"
        >
          <Paperclip size={15} />
        </button>
        <button aria-label="Attach audio" onClick={() => fileRef.current?.click()} className="rounded-lg p-1.5 text-content-3 hover:bg-island-2">
          <Mic size={15} />
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          aria-label="Attach files"
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) stageFiles(e.target.files);
            e.target.value = '';
          }}
        />
        <button
          onClick={() => void submit()}
          disabled={saving || !text.trim()}
          className="ml-auto rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
        >
          {saving ? t('inspiration.saving', 'Saving…') : t('inspiration.save', 'Save')}
        </button>
      </div>
    </div>
  );
};
