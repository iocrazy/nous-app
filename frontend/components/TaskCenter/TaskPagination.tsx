/**
 * Page-number control for the Settings → Tasks list. Prev / windowed page
 * numbers / Next + a total-count label. Pinned below the list.
 */
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** Windowed page list: always show first + last + a span around current,
 * with '…' gaps. e.g. window(6, 50) → [1, '…', 5, 6, 7, '…', 50]. */
export function pageWindow(
  current: number,
  total: number,
  span = 1,
): (number | '…')[] {
  if (total <= 1) return [1];
  const pages = new Set<number>([1, total]);
  for (let p = current - span; p <= current + span; p++) {
    if (p >= 1 && p <= total) pages.add(p);
  }
  const sorted = [...pages].sort((a, b) => a - b);
  const out: (number | '…')[] = [];
  let prev = 0;
  for (const p of sorted) {
    if (p - prev > 1) out.push('…');
    out.push(p);
    prev = p;
  }
  return out;
}

interface TaskPaginationProps {
  page: number;
  totalPages: number;
  total: number;
  onPage: (p: number) => void;
  disabled?: boolean;
}

export const TaskPagination: React.FC<TaskPaginationProps> = ({
  page,
  totalPages,
  total,
  onPage,
  disabled,
}) => {
  const { t } = useTranslation();
  if (total === 0) return null;

  const go = (p: number) => {
    if (disabled) return;
    onPage(Math.min(Math.max(1, p), totalPages));
  };

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-zinc-900/80 border-t border-zinc-800/80 text-xs shrink-0">
      <span className="text-zinc-400">
        {t('taskCenter.pagination.total', { count: total })}
      </span>
      <div className="flex-1" />
      <button
        type="button"
        onClick={() => go(page - 1)}
        disabled={disabled || page <= 1}
        className="flex items-center px-1.5 py-1 rounded text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition"
        aria-label={t('taskCenter.pagination.prev') ?? 'Previous'}
      >
        <ChevronLeft size={14} />
      </button>
      {pageWindow(page, totalPages).map((p, i) =>
        p === '…' ? (
          <span key={`gap-${i}`} className="px-1 text-zinc-600">
            …
          </span>
        ) : (
          <button
            key={p}
            type="button"
            onClick={() => go(p)}
            disabled={disabled}
            className={`min-w-[1.6rem] px-1.5 py-1 rounded tabular-nums transition disabled:opacity-40 ${
              p === page
                ? 'bg-indigo-500/20 text-indigo-200 font-medium'
                : 'text-zinc-400 hover:bg-zinc-800'
            }`}
          >
            {p}
          </button>
        ),
      )}
      <button
        type="button"
        onClick={() => go(page + 1)}
        disabled={disabled || page >= totalPages}
        className="flex items-center px-1.5 py-1 rounded text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition"
        aria-label={t('taskCenter.pagination.next') ?? 'Next'}
      >
        <ChevronRight size={14} />
      </button>
    </div>
  );
};

export default TaskPagination;
