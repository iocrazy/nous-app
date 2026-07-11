// Quick-capture box: inline #tag autocomplete, staged multi-format
// attachments (paste / drop / picker), Cmd+Enter submit.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Hash, Link as LinkIcon, Lock, Paperclip, Send, SquareCode, X } from 'lucide-react';
import { useToast } from '../Toast';
import {
  createNote,
  uploadAttachment,
  type InspirationNote,
  type NoteAttachment,
  type RefHotspot,
} from '../../services/inspirationService';
import { findActiveTag } from './noteTags';
import { continueListOnEnter, todoShortcutOnSpace } from './editorErgonomics';

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

  // memos-parity auto-grow: the box tracks its content height up to the same
  // 50vh cap memos uses (EDITOR_HEIGHT.normal), then scrolls internally.
  const autoGrow = (ta: HTMLTextAreaElement) => {
    ta.style.height = 'auto';
    ta.style.height = `${ta.scrollHeight}px`;
  };

  useEffect(() => {
    if (taRef.current) autoGrow(taRef.current);
    // grow once on mount for prefilled content (key-remount per prefill)
  }, []);

  const completeTag = (tag: string) => {
    if (!active) return;
    const next = `${text.slice(0, active.start)}#${tag} `;
    setText(next);
    setCaret(next.length);
    taRef.current?.focus();
  };

  // Toolbar quick-inserts (memos-parity): splice a snippet at the live caret
  // (or around the selection) and put the cursor where typing continues.
  const insertSnippet = (build: (selected: string, atLineStart: boolean, needsSpace: boolean) => { snippet: string; cursor: number }) => {
    const ta = taRef.current;
    const start = ta?.selectionStart ?? text.length;
    const end = ta?.selectionEnd ?? start;
    const selected = text.slice(start, end);
    const atLineStart = start === 0 || text[start - 1] === '\n';
    const needsSpace = start > 0 && !/[\s(（]/.test(text[start - 1]);
    const { snippet, cursor } = build(selected, atLineStart, needsSpace);
    const next = text.slice(0, start) + snippet + text.slice(end);
    const pos = start + cursor;
    setText(next);
    setCaret(pos);
    requestAnimationFrame(() => {
      ta?.focus();
      ta?.setSelectionRange(pos, pos);
    });
  };

  const insertTag = () =>
    insertSnippet((_sel, _ls, needsSpace) => ({
      snippet: needsSpace ? ' #' : '#',
      cursor: needsSpace ? 2 : 1,
    }));

  const insertCodeBlock = () =>
    insertSnippet((sel, atLineStart) => {
      const lead = atLineStart ? '' : '\n';
      return { snippet: `${lead}\`\`\`\n${sel}\n\`\`\`\n`, cursor: lead.length + 4 + sel.length };
    });

  const insertLink = () =>
    insertSnippet((sel) =>
      sel
        ? { snippet: `[${sel}]()`, cursor: sel.length + 3 } // cursor inside the ()
        : { snippet: '[]()', cursor: 1 }, // cursor inside the []
    );

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
    <div className="rounded-xl border border-line-strong bg-island px-4 pb-3 pt-4 shadow-sm">
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
          autoGrow(e.target);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void submit();
            return;
          }
          const ta = e.currentTarget;
          const pos = ta.selectionStart ?? text.length;
          if (ta.selectionEnd !== pos) return; // leave range selections alone
          const edit =
            e.key === 'Enter'
              ? continueListOnEnter(text, pos)
              : e.key === ' '
                ? todoShortcutOnSpace(text, pos)
                : null;
          if (edit) {
            e.preventDefault();
            setText(edit.text);
            setCaret(edit.caret);
            requestAnimationFrame(() => {
              ta.setSelectionRange(edit.caret, edit.caret);
              autoGrow(ta);
            });
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
        className="max-h-[50vh] w-full resize-none overflow-y-auto bg-transparent text-[13.5px] text-content placeholder:text-content-4 focus:outline-none"
      />

      {/* memos-parity layout: the insert-icon row sits directly under the
          text (no divider), then attachments, then a divider with the
          visibility hint on the left and a solid Save on the right. */}
      <div className="flex items-center gap-0.5">
        <button
          aria-label="Insert tag"
          title={t('inspiration.insertTag', 'Insert #tag')}
          onClick={insertTag}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-indigo-300"
        >
          <Hash size={16} />
        </button>
        <button
          aria-label="Insert code block"
          title={t('inspiration.insertCode', 'Insert code block')}
          onClick={insertCodeBlock}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-indigo-300"
        >
          <SquareCode size={16} />
        </button>
        <button
          aria-label="Attach file"
          title={t('inspiration.attachFile', 'Attach files')}
          onClick={() => fileRef.current?.click()}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-indigo-300"
        >
          <Paperclip size={16} />
        </button>
        <button
          aria-label="Insert link"
          title={t('inspiration.insertLink', 'Insert link')}
          onClick={insertLink}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-indigo-300"
        >
          <LinkIcon size={16} />
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
      </div>

      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
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

      <div className="mt-2 flex items-center justify-between border-t border-line pt-2">
        <span
          title={t('inspiration.privateHint', 'Notes are private to your account')}
          className="inline-flex cursor-default items-center gap-1.5 rounded-md px-2 py-1 text-xs text-content-3"
        >
          <Lock size={12} className="opacity-70" />
          {t('inspiration.private', 'Private')}
          <ChevronDown size={12} className="opacity-50" />
        </span>
        <button
          onClick={() => void submit()}
          disabled={saving || !text.trim()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-40"
        >
          {saving ? t('inspiration.saving', 'Saving…') : t('inspiration.save', 'Save')}
          <Send size={12} />
        </button>
      </div>
    </div>
  );
};
