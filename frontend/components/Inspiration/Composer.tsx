// Quick-capture box: TipTap-backed NoteEditor (inline #tag autocomplete,
// markdown input rules, paste/drop file interception all live there),
// staged multi-format attachments (paste / drop / picker), Cmd+Enter submit.
import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Hash, Link as LinkIcon, Paperclip, Send, SquareCode, X } from 'lucide-react';
import { useToast } from '../Toast';
import {
  createNote,
  deleteAttachment,
  uploadAttachment,
  type InspirationNote,
  type NoteAttachment,
  type RefHotspot,
} from '../../services/inspirationService';
import { NoteEditor, type NoteEditorHandle } from './NoteEditor';
import { AttachmentView } from './AttachmentView';
import { RatingStars } from '../detail/DetailCardKit';

interface BaseProps {
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
}

/** Quick-capture: the note does not exist yet, so none of the edit-only
 *  props can be meaningfully supplied. Spelling them out as `undefined`
 *  (rather than omitting them) is what makes the union discriminable — and
 *  what makes `submitLabel` without `onSubmit` a compile error instead of a
 *  silently ignored prop. */
interface CreateMode {
  onSubmit?: undefined;
  submitLabel?: undefined;
  noteId?: undefined;
  existingAttachments?: undefined;
  onAttachmentDeleted?: undefined;
}

interface EditMode {
  /**
   * EDIT MODE switch. When given, Save routes here instead of `createNote`,
   * and nothing is cleared afterwards — the parent owns closing its modal,
   * and blanking the box first would only flash an empty editor on the way
   * out. Must resolve to the post-save server row.
   *
   * Absent → the quick-capture (new note) behaviour, byte-for-byte unchanged.
   */
  onSubmit: (content: string, refHotspot?: RefHotspot) => Promise<InspirationNote>;
  /** Submit button label. Defaults to "Save". */
  submitLabel?: string;
  /** REQUIRED in edit mode: uploads and deletes must name the row they act
   *  on, and there is no id to discover — the note already exists. Making it
   *  part of the union is what removes the "edit mode but no id" branch
   *  entirely instead of guarding it at runtime. */
  noteId: string;
  /** The note's already-stored attachments, rendered with remove buttons. */
  existingAttachments?: NoteAttachment[];
  /** Called per attachment actually deleted server-side, so the card behind
   *  the modal drops it immediately — symmetric with onAttachmentUploaded.
   *  Matters when a later step of the same Save fails: the deletion already
   *  happened and the parent must not keep showing the file. */
  onAttachmentDeleted?: (noteId: string, attachmentId: string) => void;
}

type Props = (BaseProps & CreateMode) | (BaseProps & EditMode);

/**
 * One staged row per attachment the composer is responsible for.
 *
 * - `existing` — already stored server-side (edit mode only). Removing one
 *   takes it out of this list and parks it in `removed`; the DELETE fires at
 *   Save, never on click, because deletion is irreversible and Cancel must
 *   mean cancel.
 * - `pending` — a picked/pasted/dropped File not yet uploaded, tagged with
 *   the note it failed to attach to (if any) so Retry can target the right
 *   note without re-creating it.
 */
type StagedItem =
  | { kind: 'existing'; key: string; attachment: NoteAttachment }
  | { kind: 'pending'; key: string; file: File; noteId?: string };

type PendingItem = Extract<StagedItem, { kind: 'pending' }>;

export const Composer: React.FC<Props> = (props) => {
  // Base props are destructured; the MODE props stay on `props` on purpose —
  // destructuring a discriminated union loses the correlation, and reading
  // `props.noteId` inside an `if (props.onSubmit)` branch is what lets the
  // compiler know the id is there.
  const { onCreated, onAttachmentUploaded, tagSuggestions, prefill, autoFocus } = props;
  const submitLabel = props.submitLabel;
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [text, setText] = useState(prefill?.content ?? '');
  const [ref, setRef] = useState(prefill?.refHotspot ?? null);
  // Seeded once from `existingAttachments`, same initial-value semantics as
  // `prefill` — the modal remounts (keyed by note id) for each note it edits.
  const [staged, setStaged] = useState<StagedItem[]>(() =>
    (props.existingAttachments ?? []).map((attachment) => ({
      kind: 'existing' as const,
      key: `e${attachment.id}`,
      attachment,
    })),
  );
  /** Removals the user has staged but Save has not applied yet. */
  const [removed, setRemoved] = useState<NoteAttachment[]>([]);
  const [rating, setRating] = useState(0);
  const [saving, setSaving] = useState(false);
  // One boolean, derived — every edit-only / create-only branch below reads
  // this so the two modes can never drift apart.
  const isEdit = !!props.onSubmit;
  const editorRef = useRef<NoteEditorHandle>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);

  const stageFiles = useCallback((files: FileList | File[]) => {
    setStaged((prev) => [
      ...prev,
      ...Array.from(files).map((file) => ({
        kind: 'pending' as const,
        key: `f${nextKey.current++}`,
        file,
      })),
    ]);
  }, []);

  // Named distinctly from the `existingAttachments` PROP: that one is the
  // seed, this one is the live list after any staged removals.
  const storedAttachments = staged.flatMap((i) =>
    i.kind === 'existing' ? [i.attachment] : [],
  );
  const pending = staged.filter((i): i is PendingItem => i.kind === 'pending');

  /** Stage a removal: drop it from the visible list, park it for Save. */
  const removeExisting = (attachment: NoteAttachment) => {
    setStaged((prev) =>
      prev.filter((i) => !(i.kind === 'existing' && i.attachment.id === attachment.id)),
    );
    setRemoved((prev) => [...prev, attachment]);
  };

  const uploadFailedMsg = (name: string, err: unknown) =>
    t('inspiration.uploadFailed', 'Upload failed: {{name}}', { name }) +
    `: ${(err as Error).message}`;

  /**
   * Edit mode Save. Order is load-bearing:
   *   uploads → removals → content PATCH.
   * The PATCH response carries the note's attachment list, so it has to run
   * LAST or the parent would store a list that predates this very save.
   *
   * Any failure ABORTS the rest and leaves the modal open. Closing on a
   * failed upload would take the only copy of the file with it, leaving a
   * transient toast as the sole trace; every failure here is typed and
   * user-visible, never a silent no-op.
   *
   * Returns false when it aborted.
   */
  const applyAttachmentChanges = async (noteId: string): Promise<boolean> => {
    for (const item of pending) {
      try {
        const attachment = await uploadAttachment(noteId, item.file);
        setStaged((prev) => prev.filter((x) => x.key !== item.key));
        onAttachmentUploaded?.(noteId, attachment);
      } catch (err) {
        addToast(uploadFailedMsg(item.file.name, err), 'error');
        // Tag it so the Retry action appears, same as the create path.
        setStaged((prev) =>
          prev.map((x) => (x.key === item.key && x.kind === 'pending' ? { ...x, noteId } : x)),
        );
        return false;
      }
    }
    for (const attachment of removed) {
      try {
        await deleteAttachment(attachment.id);
        // Drop it from the pending-removal set as soon as it lands, so a
        // failure of a LATER step can't make the next Save re-issue a DELETE
        // for a row that is already gone (404 → permanently unsaveable modal).
        setRemoved((prev) => prev.filter((x) => x.id !== attachment.id));
        props.onAttachmentDeleted?.(noteId, attachment.id);
      } catch (err) {
        addToast(
          t('inspiration.removeFailed', 'Remove failed: {{name}}', {
            name: attachment.original_name,
          }) + `: ${(err as Error).message}`,
          'error',
        );
        // Put it back: the UI must not claim a removal the server refused.
        setRemoved((prev) => prev.filter((x) => x.id !== attachment.id));
        setStaged((prev) => [
          ...prev,
          { kind: 'existing', key: `e${attachment.id}`, attachment },
        ]);
        return false;
      }
    }
    return true;
  };

  const submit = async () => {
    const content = text.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      if (props.onSubmit) {
        if (!(await applyAttachmentChanges(props.noteId))) return;
        await props.onSubmit(content, ref ?? undefined);
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
      const failed: StagedItem[] = [];
      // Create mode has no `existing` rows, so `pending` is the whole list.
      for (const item of pending) {
        try {
          uploaded.push(await uploadAttachment(note.id, item.file));
        } catch (err) {
          addToast(uploadFailedMsg(item.file.name, err), 'error');
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

  const retryUpload = async (item: PendingItem) => {
    if (!item.noteId) return;
    try {
      const attachment = await uploadAttachment(item.noteId, item.file);
      setStaged((prev) => prev.filter((s) => s.key !== item.key));
      onAttachmentUploaded?.(item.noteId, attachment);
    } catch (err) {
      addToast(uploadFailedMsg(item.file.name, err), 'error');
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

      {/* Already-stored files (edit mode). Rendered only when non-empty — not
          merely a tidiness choice: AttachmentView reads the auth context for
          its media token, so mounting it in the quick-capture box (which has
          nothing to show) would drag that dependency into every create-mode
          consumer for no benefit. */}
      {storedAttachments.length > 0 && (
        <AttachmentView attachments={storedAttachments} onDelete={removeExisting} />
      )}

      {pending.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-2">
          {pending.map((item) => (
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
