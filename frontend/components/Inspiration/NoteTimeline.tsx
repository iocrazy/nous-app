// Main note feed (spec §2.2 #4): groups notes by note_date with a day
// header + divider, renders NoteCard per row, and a trailing "load more"
// affordance for keyset pagination.
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { NoteCard } from './NoteCard';
import type { InspirationNote } from '../../services/inspirationService';
import { formatDateShort } from '../../utils/formatDate';

interface Props {
  notes: InspirationNote[];
  onEdit: (n: InspirationNote) => void;
  onTogglePin: (n: InspirationNote) => void;
  onDelete: (n: InspirationNote) => void;
  onTagClick: (tag: string) => void;
  onToggleTask?: (note: InspirationNote, index: number) => void;
  hasMore: boolean;
  loading: boolean;
  loadMore: () => void;
}

export const NoteTimeline: React.FC<Props> = ({
  notes, onEdit, onTogglePin, onDelete, onTagClick, onToggleTask, hasMore, loading, loadMore,
}) => {
  const { t } = useTranslation();
  const groups = useMemo(() => {
    const byDay = new Map<string, InspirationNote[]>();
    for (const n of notes) {
      const list = byDay.get(n.note_date) ?? [];
      list.push(n);
      byDay.set(n.note_date, list);
    }
    return Array.from(byDay.entries());
  }, [notes]);

  if (!notes.length && !loading) {
    return (
      <div className="rounded-xl bg-island px-4 py-10 text-center text-sm text-content-3">
        {t('inspiration.empty', 'No notes yet — capture your first idea above.')}
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      {groups.map(([day, dayNotes]) => (
        <React.Fragment key={day}>
          <div className="flex items-center gap-2.5 px-0.5 pt-1 text-[11px] font-semibold uppercase tracking-wide text-content-3 tabular-nums">
            {formatDateShort(day)}
            <span className="font-normal text-content-4">
              · {t('inspiration.noteCount', '{{count}} notes', { count: dayNotes.length })}
            </span>
            <span className="h-px flex-1 bg-line" />
          </div>
          {dayNotes.map((n) => (
            <NoteCard
              key={n.id}
              note={n}
              onEdit={onEdit}
              onTogglePin={onTogglePin}
              onDelete={onDelete}
              onTagClick={onTagClick}
              onToggleTask={onToggleTask}
            />
          ))}
        </React.Fragment>
      ))}
      {hasMore && (
        <button
          onClick={loadMore}
          disabled={loading}
          className="w-full rounded-xl bg-island py-2 text-xs text-content-3 hover:text-content-2 disabled:opacity-50"
        >
          {loading ? t('inspiration.loading', 'Loading…') : t('inspiration.loadMore', 'Load more')}
        </button>
      )}
    </div>
  );
};
