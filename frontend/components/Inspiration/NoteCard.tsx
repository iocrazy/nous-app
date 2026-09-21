// Single note card in the inspiration timeline (spec §2.2 #4):
// timestamp + pin badge + "···" actions menu, markdown body, optional
// ref_hotspot reference card, tag chips, attachments.
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Archive, ArchiveRestore, ExternalLink, Flame, MoreHorizontal, Pin } from 'lucide-react';
import { NoteMarkdown } from './NoteMarkdown';
import { AttachmentView } from './AttachmentView';
import { RatingStars } from '../detail/DetailCardKit';
import type { InspirationNote } from '../../services/inspirationService';

interface Props {
  note: InspirationNote;
  onEdit: (note: InspirationNote) => void;
  onTogglePin: (note: InspirationNote) => void;
  onDelete: (note: InspirationNote) => void;
  /** Archive the note, or restore it when it already is (mig 478). Omitted →
   *  the menu item is not rendered, same optional-affordance convention as
   *  onToggleTask. Which of the two it offers follows `note.archived_at`, not
   *  a prop, so a card can never offer "Archive" on an archived note. */
  onArchive?: (note: InspirationNote) => void;
  onTagClick: (tag: string) => void;
  onToggleTask?: (note: InspirationNote, index: number) => void;
  /** Rate the note 0-5 (mig 448). Omitted → the stars are not rendered at all,
   *  same optional-affordance convention as onToggleTask. */
  onRating?: (note: InspirationNote, value: number) => void;
  /** Open the beats timeline this note is pinned to (Beats M4 reverse-nav). */
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const NoteCard: React.FC<Props> = ({ note, onEdit, onTogglePin, onDelete, onArchive, onTagClick, onToggleTask, onRating }) => {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  /**
   * Run a menu action, handing focus back to the trigger FIRST.
   *
   * Standard dropdown behaviour, and load-bearing for the edit modal: the
   * item you click lives inside `{menuOpen && …}`, so the same click that
   * runs the action also unmounts the element the browser had just focused —
   * and `document.activeElement` falls back to `<body>`. Anything that reads
   * activeElement afterwards to remember "where focus came from" (the modal's
   * focus-restore effect does exactly that) would capture body and restore to
   * nothing. jsdom hides this: its fireEvent.click never moves focus at all.
   */
  const runMenuAction = (action: () => void) => {
    setMenuOpen(false);
    triggerRef.current?.focus();
    action();
  };

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menuOpen]);

  return (
    <div className="rounded-xl bg-island px-4 py-3">
      <div className="flex items-center gap-2 text-[11px] text-content-3 tabular-nums">
        <span>{timeOf(note.created_at)}</span>
        {note.pinned && <Pin size={11} className="text-[var(--accent-text)]" />}
        {/* role="group" on the wrapper is load-bearing, not decoration: a bare
            <div> is role=generic, where ARIA prohibits naming, so the
            aria-label would be dropped by screen readers while still
            satisfying getByLabelText. Two unnamed star rows (card + composer)
            writing different notes is exactly the ambiguity this prevents. */}
        {onRating && (
          <div role="group" aria-label={t('inspiration.rating', 'Rating')}>
            <RatingStars value={note.rating ?? 0} onChange={(v) => onRating(note, v)} size={12} />
          </div>
        )}
        <div className="relative ml-auto" ref={menuRef}>
          <button
            ref={triggerRef}
            aria-label="Note actions"
            onClick={() => setMenuOpen((v) => !v)}
            className="rounded p-1 text-content-3 hover:bg-island-2 hover:text-content-2"
          >
            <MoreHorizontal size={15} />
          </button>
          {/* role="menu" so the items inside can be addressed as a set. Two
              buttons on this page read "Archive" — this one and the
              Active/Archived view switch — and naming the container is what
              keeps them apart for a screen reader as well as for a test. */}
          {menuOpen && (
            <div
              role="menu"
              aria-label={t('inspiration.noteActions', 'Note actions')}
              className="absolute right-0 z-10 mt-1 w-36 rounded-lg border border-line bg-island-2 py-1 text-xs text-content-2 shadow-lg"
            >
              <button className="block w-full px-3 py-1.5 text-left hover:bg-line" onClick={() => runMenuAction(() => onEdit(note))}>
                {t('inspiration.edit', 'Edit')}
              </button>
              {/* Not offered on an archived note: a pin is a claim on the top
                  of the live list, which this note is not in. Archiving gives
                  the pin up (mig 478), so offering it back here would rebuild
                  the state the archive just cleared. */}
              {!note.archived_at && (
                <button className="block w-full px-3 py-1.5 text-left hover:bg-line" onClick={() => runMenuAction(() => onTogglePin(note))}>
                  {note.pinned ? t('inspiration.unpin', 'Unpin') : t('inspiration.pin', 'Pin')}
                </button>
              )}
              {onArchive && (
                <button
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-line"
                  onClick={() => runMenuAction(() => onArchive(note))}
                >
                  {note.archived_at ? (
                    <>
                      <ArchiveRestore size={12} /> {t('inspiration.unarchive', 'Unarchive')}
                    </>
                  ) : (
                    <>
                      <Archive size={12} /> {t('inspiration.archive', 'Archive')}
                    </>
                  )}
                </button>
              )}
              <button className="block w-full px-3 py-1.5 text-left text-danger hover:bg-line" onClick={() => runMenuAction(() => onDelete(note))}>
                {t('inspiration.delete', 'Delete')}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="mt-1.5 text-[13.5px] leading-relaxed text-content">
        <NoteMarkdown
          source={note.content_md}
          onToggleTask={onToggleTask ? (index) => onToggleTask(note, index) : undefined}
          onTagClick={onTagClick}
        />
      </div>

      {note.ref_hotspot && (
        <div className="mt-2 rounded-lg border border-line border-l-2 border-l-indigo-500 bg-island-2 px-3 py-2">
          <div className="flex items-center gap-2 text-[10px] text-content-3">
            {note.ref_hotspot.source && (
              <span className="rounded bg-line px-1.5 py-0.5 font-bold text-content-2">{note.ref_hotspot.source}</span>
            )}
          </div>
          <div className="mt-1 text-[13px] font-semibold text-content">{note.ref_hotspot.title}</div>
          <div className="mt-1 flex items-center gap-3 text-[11px]">
            {typeof note.ref_hotspot.heat === 'number' && (
              <span className="inline-flex items-center gap-1 text-amber-400">
                <Flame size={11} /> {note.ref_hotspot.heat}
              </span>
            )}
            {note.ref_hotspot.url && (
              <a href={note.ref_hotspot.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[var(--accent-text)] hover:underline">
                {t('inspiration.openSource', 'Open source')} <ExternalLink size={10} />
              </a>
            )}
          </div>
        </div>
      )}

      {/* Tags now render inline in the body via NoteMarkdown's tag chips
          (they are highlighted #tag occurrences in content_md); the former
          duplicate below-body chip row was removed (2026-07-19). */}


      <AttachmentView attachments={note.attachments} />
    </div>
  );
};
