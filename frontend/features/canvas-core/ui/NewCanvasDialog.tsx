/**
 * NewCanvasDialog — the Infinite-Canvas-style create dialog: optional name,
 * Create/Cancel. Shared by every "New Canvas" entry point (workspace Canvas
 * module, canvas landing page). The kind is no longer chosen here — classic
 * is retired, so the backend default (smart) always applies.
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40';

export interface NewCanvasDialogProps {
  open: boolean;
  creating: boolean;
  onCancel: () => void;
  onCreate: (payload: { name: string }) => void;
}

export function NewCanvasDialog({
  open,
  creating,
  onCancel,
  onCreate,
}: NewCanvasDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  // Fresh form per open, and focus the name field.
  useEffect(() => {
    if (!open) return;
    setName('');
    inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const submit = () => {
    if (creating) return;
    onCreate({ name: name.trim() });
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-canvas-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !creating) onCancel();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !creating) onCancel();
      }}
    >
      <div className="w-[340px] max-w-full rounded-2xl border border-ink-800 bg-ink-900 p-5 shadow-2xl">
        <h2 id="new-canvas-title" className="text-sm font-semibold text-ink-100">
          {t('canvasList.newCanvas', 'New Canvas')}
        </h2>

        <input
          ref={inputRef}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
          }}
          placeholder={t('canvasList.namePlaceholder', 'Canvas name (optional)')}
          className={`mt-4 w-full rounded-lg border border-ink-700 bg-ink-950/40 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-500 ${FOCUS_RING}`}
        />

        <div className="mt-4 flex gap-2">
          <button
            type="button"
            onClick={submit}
            disabled={creating}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-60 ${FOCUS_RING}`}
          >
            {creating && <Loader2 size={14} className="animate-spin" />}
            {t('common.create', 'Create')}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={creating}
            className={`flex-1 rounded-lg border border-ink-700 px-3 py-2 text-sm text-ink-300 transition-colors hover:bg-ink-800 disabled:opacity-60 ${FOCUS_RING}`}
          >
            {t('common.cancel', 'Cancel')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default NewCanvasDialog;
