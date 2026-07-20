/**
 * MemoQuickCard — the laper-style quick-capture popover for a timeline memo pin.
 *
 * Opened by clicking the memo rail (create) or a pin's Edit (edit). Carries an
 * auto-filled timestamp chip, a post-style body textarea, up to four images, and
 * a Publish / Save action. This component only collects the File objects and
 * hands them back on submit; the rail owns the memo REST writes (uploadMemoImage
 * → createMemo/updateMemo). Existing images (edit mode) arrive as pre-built,
 * token-bearing URLs so this stays a thin controlled popover the rail can
 * unit-test without a live upload.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ImagePlus, Trash2, X } from 'lucide-react';

import { formatAnchorSec } from './memoGeometry';

/** Max images per memo (spec: ≤4). Existing + pending are counted together. */
export const MEMO_MAX_IMAGES = 4;

interface Props {
  mode: 'create' | 'edit';
  /** Anchor offset in seconds — drives the timestamp chip. */
  sec: number;
  /** Left position (px) of the card within the rail. */
  x: number;
  initialContent?: string;
  /** Pre-built URLs for existing memo images (edit mode). */
  existingImageUrls?: string[];
  busy?: boolean;
  /** create: publish a brand-new memo. */
  onPublish?: (content: string, files: File[]) => void;
  /** edit: save content + any newly added images. */
  onSave?: (content: string, files: File[]) => void;
  /** edit: delete the memo outright. */
  onDelete?: () => void;
  onClose: () => void;
}

export function MemoQuickCard({
  mode,
  sec,
  x,
  initialContent = '',
  existingImageUrls = [],
  busy = false,
  onPublish,
  onSave,
  onDelete,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const [content, setContent] = useState(initialContent);
  const [files, setFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Object URLs for pending file previews — revoked on change/unmount.
  const previews = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(
    () => () => {
      previews.forEach((u) => URL.revokeObjectURL(u));
    },
    [previews],
  );

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const remaining = MEMO_MAX_IMAGES - existingImageUrls.length - files.length;

  const onPickFiles = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const picked = Array.from(e.target.files ?? []).filter((f) =>
        f.type.startsWith('image/'),
      );
      if (picked.length) setFiles((prev) => [...prev, ...picked].slice(0, MEMO_MAX_IMAGES));
      e.target.value = ''; // allow re-picking the same file
    },
    [],
  );

  const removePending = useCallback((idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const canSubmit = (content.trim().length > 0 || files.length > 0) && !busy;

  const submit = useCallback(() => {
    if (!canSubmit) return;
    if (mode === 'create') onPublish?.(content.trim(), files);
    else onSave?.(content.trim(), files);
  }, [canSubmit, mode, content, files, onPublish, onSave]);

  return (
    <div
      className="mh-memo-quick"
      data-testid="memo-quick-card"
      style={{ left: `${x}px` }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="mh-memo-quick-head">
        <span className="mh-memo-quick-chip" data-testid="memo-quick-chip">
          {formatAnchorSec(sec)}
        </span>
        <button
          type="button"
          className="mh-memo-quick-x"
          aria-label={t('common.cancel')}
          onClick={onClose}
        >
          <X size={14} />
        </button>
      </div>

      <textarea
        ref={textareaRef}
        className="mh-memo-quick-body"
        data-testid="memo-quick-body"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={t('editor.memoQuickPlaceholder')}
        rows={3}
      />

      {(existingImageUrls.length > 0 || files.length > 0) && (
        <div className="mh-memo-quick-thumbs" data-testid="memo-quick-thumbs">
          {existingImageUrls.map((url) => (
            <img key={url} className="mh-memo-quick-thumb" src={url} alt="" />
          ))}
          {previews.map((url, i) => (
            <span key={url} className="mh-memo-quick-thumb-wrap">
              <img className="mh-memo-quick-thumb" src={url} alt="" />
              <button
                type="button"
                className="mh-memo-quick-thumb-x"
                aria-label={t('common.delete')}
                onClick={() => removePending(i)}
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        data-testid="memo-quick-file"
        onChange={onPickFiles}
      />

      <div className="mh-memo-quick-foot">
        <button
          type="button"
          className="mh-memo-quick-attach"
          data-testid="memo-quick-attach"
          disabled={remaining <= 0}
          onClick={() => fileInputRef.current?.click()}
          aria-label={t('editor.memoAddImage')}
        >
          <ImagePlus size={15} />
          {remaining > 0 && <span>{remaining}</span>}
        </button>
        <span className="mh-memo-quick-foot-spacer" />
        {mode === 'edit' && (
          <button
            type="button"
            className="mh-memo-quick-ghost"
            data-testid="memo-quick-delete"
            onClick={onDelete}
            aria-label={t('editor.memoDelete')}
          >
            <Trash2 size={14} />
          </button>
        )}
        <button
          type="button"
          className="mh-memo-quick-publish"
          data-testid="memo-quick-publish"
          disabled={!canSubmit}
          onClick={submit}
        >
          {mode === 'create' ? t('editor.memoPublish') : t('common.save')}
        </button>
      </div>
    </div>
  );
}
