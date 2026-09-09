/**
 * "Fork from step N" (harness 2b-1 §2): confirm a branch point and optionally
 * say what to do differently. The steer becomes the forked turn's user
 * message; empty means "just continue from here".
 */
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GitFork, X } from 'lucide-react';

export interface ForkRunDialogProps {
  /** Position shown to the user (the scrubber's step index, 1-based). */
  stepLabel: string;
  pending: boolean;
  error: string | null;
  onConfirm: (steer: string | undefined) => void;
  onCancel: () => void;
}

export const ForkRunDialog: React.FC<ForkRunDialogProps> = ({ stepLabel, pending, error, onConfirm, onCancel }) => {
  const { t } = useTranslation();
  const [steer, setSteer] = useState('');
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    box.current?.focus();
  }, []);
  const submit = () => {
    if (pending) return;
    const text = steer.trim();
    onConfirm(text ? text : undefined);
  };
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/70"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onCancel();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="fork-dialog-title"
        data-testid="fork-dialog"
        className="w-[min(520px,92vw)] rounded-lg border border-info-line bg-ink-950 p-4 shadow-xl"
      >
        <div className="flex items-center gap-2">
          <GitFork size={14} className="text-info" />
          <h2 id="fork-dialog-title" className="text-[14px] font-medium text-ink-100">
            {t('fork.title', 'Fork from {{step}}', { step: stepLabel })}
          </h2>
          <button type="button" onClick={onCancel} className="ml-auto text-ink-500 hover:text-ink-200" aria-label={t('common.close', 'Close')}>
            <X size={14} />
          </button>
        </div>
        <p className="mt-2 text-[13px] text-ink-400 leading-relaxed">
          {t('fork.body', 'A new run starts from this point with the same history. The original run and its conversation are kept as they are.')}
        </p>
        <label className="mt-3 block text-[12px] text-ink-400" htmlFor="fork-steer">
          {t('fork.steerLabel', 'From here, do this instead (optional)')}
        </label>
        <textarea
          id="fork-steer"
          ref={box}
          data-testid="fork-steer"
          value={steer}
          maxLength={4000}
          onChange={(e) => setSteer(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit();
          }}
          rows={3}
          className="mt-1 w-full rounded border border-ink-800 bg-ink-900/60 px-2 py-1.5 text-[13px] text-ink-100 focus:border-info-line focus:outline-none"
          placeholder={t('fork.steerPlaceholder', 'e.g. Make act two darker; keep the ending.')}
        />
        {error && (
          <p data-testid="fork-error" className="mt-2 text-[12px] text-danger break-words">
            {error}
          </p>
        )}
        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="fork-cancel"
            onClick={onCancel}
            disabled={pending}
            className="rounded border border-ink-700 px-3 py-1.5 text-[13px] text-ink-300 hover:border-ink-500 disabled:opacity-50"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            data-testid="fork-confirm"
            onClick={submit}
            disabled={pending}
            className="inline-flex items-center gap-1 rounded border border-info-line bg-info-soft px-3 py-1.5 text-[13px] text-info hover:brightness-110 disabled:opacity-50"
          >
            <GitFork size={12} /> {pending ? t('fork.forking', 'Forking…') : t('fork.confirm', 'Fork')}
          </button>
        </div>
      </div>
    </div>
  );
};
