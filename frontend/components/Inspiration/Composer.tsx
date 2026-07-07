// Quick-capture box: inline #tag autocomplete, staged multi-format
// attachments (paste / drop / picker), Cmd+Enter submit.
import React, { useCallback, useMemo, useRef, useState } from 'react';
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
  tagSuggestions: string[];
  prefill?: { content: string; refHotspot?: RefHotspot } | null;
}

export const Composer: React.FC<Props> = ({ onCreated, tagSuggestions, prefill }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [text, setText] = useState(prefill?.content ?? '');
  const [staged, setStaged] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [caret, setCaret] = useState(0);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

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
    setStaged((prev) => [...prev, ...Array.from(files)]);
  }, []);

  const submit = async () => {
    const content = text.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      const note = await createNote(content, prefill?.refHotspot);
      const uploaded: NoteAttachment[] = [];
      for (const file of staged) {
        try {
          uploaded.push(await uploadAttachment(note.id, file));
        } catch (err) {
          addToast(
            t('inspiration.uploadFailed', 'Upload failed: {{name}}', { name: file.name }) +
              `: ${(err as Error).message}`,
            'error',
          );
        }
      }
      onCreated({ ...note, attachments: [...note.attachments, ...uploaded] });
      setText('');
      setStaged([]);
    } catch (err) {
      addToast((err as Error).message, 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl bg-island px-4 pb-3 pt-4">
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
          {staged.map((f, i) => (
            <span key={`${f.name}-${i}`} className="inline-flex items-center gap-1.5 rounded-lg bg-island-2 px-2.5 py-1 text-xs text-content-2">
              {f.name}
              <button
                aria-label={`Remove ${f.name}`}
                onClick={() => setStaged((prev) => prev.filter((_, j) => j !== i))}
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
