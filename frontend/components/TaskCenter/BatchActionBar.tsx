/**
 * Floating batch-action bar for Task Center multi-select.
 *
 * Appears only when ≥1 terminal task is selected. Retry acts on the
 * retryable subset (failed/cancelled, non-permanent); Delete acts on the
 * whole selection. While a batch runs, both actions disable and a live
 * "12/31" progress label replaces the counts.
 */
import React from 'react';
import { RotateCw, Trash2, CheckSquare, X, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface BatchActionBarProps {
  selectedCount: number;
  retryableCount: number;
  onRetry: () => void;
  onDelete: () => void;
  onSelectAll: () => void;
  onClear: () => void;
  busy: boolean;
  progress: { done: number; total: number } | null;
  /** Cross-page select-all is active (selectedCount spans all pages). */
  allMatchingActive?: boolean;
  /** Total terminal tasks matching the current filter (for the affordance). */
  matchTotal?: number;
  /** Provided when more matches exist beyond the current page → show the link. */
  onSelectAllMatching?: () => void;
}

export const BatchActionBar: React.FC<BatchActionBarProps> = ({
  selectedCount,
  retryableCount,
  onRetry,
  onDelete,
  onSelectAll,
  onClear,
  busy,
  progress,
  allMatchingActive,
  matchTotal,
  onSelectAllMatching,
}) => {
  const { t } = useTranslation();

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-zinc-900/95 border-t border-indigo-500/30 shadow-[0_-4px_12px_rgba(0,0,0,0.3)] text-xs shrink-0">
      {busy && progress ? (
        <span className="flex items-center gap-2 text-indigo-300">
          <Loader2 size={13} className="animate-spin" />
          {t('taskCenter.batch.running', {
            done: progress.done,
            total: progress.total,
          })}
        </span>
      ) : (
        <span className="text-zinc-300 font-medium">
          {allMatchingActive
            ? t('taskCenter.batch.selectedAllMatching', { count: selectedCount })
            : t('taskCenter.batch.selected', { count: selectedCount })}
        </span>
      )}

      {!busy && onSelectAllMatching && (
        <button
          type="button"
          onClick={onSelectAllMatching}
          className="text-indigo-300 hover:text-indigo-200 underline underline-offset-2 transition"
        >
          {t('taskCenter.batch.selectAllMatching', { count: matchTotal ?? 0 })}
        </button>
      )}

      <div className="flex-1" />

      <button
        type="button"
        onClick={onSelectAll}
        disabled={busy}
        className="flex items-center gap-1.5 px-2 py-1 rounded text-zinc-300 hover:bg-zinc-800 disabled:opacity-40 transition"
        title={t('taskCenter.batch.selectAll') ?? ''}
      >
        <CheckSquare size={13} />
        {t('taskCenter.batch.selectAll')}
      </button>

      <button
        type="button"
        onClick={onRetry}
        disabled={busy || retryableCount === 0}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-indigo-500/15 text-indigo-200 hover:bg-indigo-500/25 disabled:opacity-40 disabled:hover:bg-indigo-500/15 transition"
      >
        <RotateCw size={13} />
        {t('taskCenter.batch.retry', { count: retryableCount })}
      </button>

      <button
        type="button"
        onClick={onDelete}
        disabled={busy || selectedCount === 0}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-rose-500/10 text-rose-300 hover:bg-rose-500/20 disabled:opacity-40 transition"
      >
        <Trash2 size={13} />
        {t('taskCenter.batch.delete', { count: selectedCount })}
      </button>

      <button
        type="button"
        onClick={onClear}
        disabled={busy}
        className="flex items-center gap-1 px-2 py-1 rounded text-zinc-400 hover:bg-zinc-800 disabled:opacity-40 transition"
        title={t('taskCenter.batch.clear') ?? ''}
      >
        <X size={13} />
      </button>
    </div>
  );
};

export default BatchActionBar;
