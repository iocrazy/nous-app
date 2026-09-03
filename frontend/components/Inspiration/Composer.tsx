// Quick-capture box: TipTap-backed NoteEditor (inline #tag autocomplete,
// markdown input rules, paste/drop file interception all live there),
// staged multi-format attachments (paste / drop / picker), Cmd+Enter submit.
import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Hash, Link as LinkIcon, Paperclip, Send, SquareCode, X } from 'lucide-react';
import { useToast } from '../Toast';
import {
  createNote,
  uploadAttachment,
  type InspirationNote,
  type NoteAttachment,
  type RefHotspot,
} from '../../services/inspirationService';
import { NoteEditor, type NoteEditorHandle } from './NoteEditor';
import { RatingStars } from '../detail/DetailCardKit';

interface Props {
  onCreated: (note: InspirationNote) => void;
  /** Called when a retried upload finally succeeds, so the page can merge the
   * new attachment into the (already-created) note's card. */
  onAttachmentUploaded?: (noteId: string, attachment: NoteAttachment) => void;
  tagSuggestions: string[];
  prefill?: { content: string; refHotspot?: RefHotspot } | null;
  /** Focus the editor (caret at document start, before any prefilled tag
   * line) on mount — used when a save-as-note prefill just landed. Composer
   * remounts (keyed by prefillNonce) on every new prefill, so this only
   * needs to run once per mount, not react to later changes. */
  autoFocus?: boolean;
  /**
   * EDIT MODE switch. When given, Save routes here instead of `createNote`,
   * and nothing is cleared afterwards — the parent owns closing its modal,
   * and blanking the box first would only flash an empty editor on the way
   * out. Must resolve to the post-save server row.
   *
   * Absent → the quick-capture (new note) behaviour, byte-for-byte unchanged.
   */
  onSubmit?: (content: string, refHotspot?: RefHotspot) => Promise<InspirationNote>;
  /** Submit button label. Defaults to "Save". */
  submitLabel?: string;
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
  onSubmit,
  submitLabel,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [text, setText] = useState(prefill?.content ?? '');
  const [ref, setRef] = useState(prefill?.refHotspot ?? null);
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [rating, setRating] = useState(0);
  const [saving, setSaving] = useState(false);
  // One boolean, derived — every edit-only / create-only branch below reads
  // this so the two modes can never drift apart.
  const isEdit = !!onSubmit;
  const editorRef = useRef<NoteEditorHandle>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);

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
      if (onSubmit) {
        await onSubmit(content, ref ?? undefined);
        // Deliberately no clearing/reset: see the `onSubmit` prop doc.
        return;
      }
      // `rating > 0 ? 3-arg : 2-arg` rather than always passing `rating`:
      // 0 IS the DB default, so an untouched star row must not put a
      // "rated zero" claim in the request body (nor change the existing
      // two-arg call shape every other caller and test already pins).
      const note = rating > 0
        ? await createNote(content, ref ?? undefined, rating)
        : await createNote(content, ref ?? undefined);
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
      setRating(0);
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
          {/* Read-only in edit mode ON PURPOSE: NoteUpdateIn accepts only
              content_md / pinned / rating, so a "removed" reference could
              never be persisted — the button would be a silent no-op. */}
          {!isEdit && (
            <button
              aria-label="Remove hotspot reference"
              onClick={() => setRef(null)}
              className="shrink-0 text-content-3 hover:text-content"
            >
              <X size={13} />
            </button>
          )}
        </div>
      )}
      <NoteEditor
        ref={editorRef}
        value={text}
        onChange={setText}
        placeholder={t('inspiration.placeholder', 'Capture an idea… #tag inline, paste an image, or drop any file')}
        autoFocus={autoFocus}
        onSubmit={() => void submit()}
        onFiles={stageFiles}
        tagSuggestions={tagSuggestions}
      />

      {/* memos-parity layout: the insert-icon row sits directly under the
          text (no divider), then attachments, then a divider with the
          visibility hint on the left and a solid Save on the right. */}
      <div className="flex items-center gap-0.5">
        <button
          aria-label="Insert tag"
          title={t('inspiration.insertTag', 'Insert #tag')}
          onClick={() => editorRef.current?.insertTag()}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-[var(--accent-text)]"
        >
          <Hash size={16} />
        </button>
        <button
          aria-label="Insert code block"
          title={t('inspiration.insertCode', 'Insert code block')}
          onClick={() => editorRef.current?.insertCodeBlock()}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-[var(--accent-text)]"
        >
          <SquareCode size={16} />
        </button>
        <button
          aria-label="Attach file"
          title={t('inspiration.attachFile', 'Attach files')}
          onClick={() => fileRef.current?.click()}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-[var(--accent-text)]"
        >
          <Paperclip size={16} />
        </button>
        <button
          aria-label="Insert link"
          title={t('inspiration.insertLink', 'Insert link')}
          onClick={() => editorRef.current?.insertLink()}
          className="rounded-md p-1.5 text-content-3 hover:bg-island-2 hover:text-[var(--accent-text)]"
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
                  className="font-semibold text-[var(--accent-text)] hover:text-[var(--accent-text)]"
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

      {/* The left slot used to hold a decorative "Private" pill: a <span> with
          a chevron, no onClick, over a schema with no visibility column. It
          promised a choice nothing could make, so it is gone. The rating that
          replaced it is real — createNote takes it in the SAME request, which
          is what lets an external client (the iOS Shortcut) post body+rating
          without a follow-up PATCH.

          Edit mode leaves the slot empty rather than showing a second set of
          stars: NoteCard's stars already write this field (with a per-note
          seq guard), and two writers for one value would need a "who wins"
          story that nothing here provides. */}
      <div className="mt-2 flex items-center justify-between border-t border-line pt-2">
        {isEdit ? (
          <div />
        ) : (
          <div aria-label={t('inspiration.rating', 'Rating')} className="px-1">
            <RatingStars value={rating} onChange={setRating} size={14} />
          </div>
        )}
        <button
          onClick={() => void submit()}
          disabled={saving || !text.trim()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-40"
        >
          {saving
            ? t('inspiration.saving', 'Saving…')
            : submitLabel ?? t('inspiration.save', 'Save')}
          <Send size={12} />
        </button>
      </div>
    </div>
  );
};
