// Single note card in the inspiration timeline (spec §2.2 #4):
// timestamp + pin badge + "···" actions menu, markdown body, optional
// ref_hotspot reference card, tag chips, attachments.
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Anchor, ExternalLink, Flame, MoreHorizontal, Pin } from 'lucide-react';
import { NoteMarkdown } from './NoteMarkdown';
import { AttachmentView } from './AttachmentView';
import type { InspirationNote } from '../../services/inspirationService';

interface Props {
  note: InspirationNote;
  onEdit: (note: InspirationNote) => void;
  onTogglePin: (note: InspirationNote) => void;
  onDelete: (note: InspirationNote) => void;
  onTagClick: (tag: string) => void;
  onToggleTask?: (note: InspirationNote, index: number) => void;
  /** Open the beats timeline this note is pinned to (Beats M4 reverse-nav). */
  onOpenAnchor?: (note: InspirationNote) => void;
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** Film-apostrophe offset for the anchor chip (`45s`, `1'`, `1'30`). */
function anchorLabel(sec: number): string {
  const whole = Math.max(0, Math.round(sec));
  if (whole < 60) return `${whole}s`;
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return s === 0 ? `${m}'` : `${m}'${String(s).padStart(2, '0')}`;
}

export const NoteCard: React.FC<Props> = ({ note, onEdit, onTogglePin, onDelete, onTagClick, onToggleTask, onOpenAnchor }) => {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

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
        <div className="relative ml-auto" ref={menuRef}>
          <button
            aria-label="Note actions"
            onClick={() => setMenuOpen((v) => !v)}
            className="rounded p-1 text-content-3 hover:bg-island-2 hover:text-content-2"
          >
            <MoreHorizontal size={15} />
          </button>
          {menuOpen && (
            <div className="absolute right-0 z-10 mt-1 w-32 rounded-lg border border-line bg-island-2 py-1 text-xs text-content-2 shadow-lg">
              <button className="block w-full px-3 py-1.5 text-left hover:bg-line" onClick={() => { setMenuOpen(false); onEdit(note); }}>
                {t('inspiration.edit', 'Edit')}
              </button>
              <button className="block w-full px-3 py-1.5 text-left hover:bg-line" onClick={() => { setMenuOpen(false); onTogglePin(note); }}>
                {note.pinned ? t('inspiration.unpin', 'Unpin') : t('inspiration.pin', 'Pin')}
              </button>
              <button className="block w-full px-3 py-1.5 text-left text-red-400 hover:bg-line" onClick={() => { setMenuOpen(false); onDelete(note); }}>
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

      {note.anchor_script_id && note.anchor_sec != null && (
        <button
          type="button"
          data-testid="note-anchor-chip"
          onClick={() => onOpenAnchor?.(note)}
          disabled={!onOpenAnchor}
          className="mt-2 inline-flex items-center gap-1 rounded-full bg-island-2 px-2 py-0.5 text-[10.5px] font-semibold text-content-2 tabular-nums hover:text-content disabled:cursor-default disabled:hover:text-content-2"
          title={t('editor.memoAnchorChip', 'Pinned to a script timeline at {{time}}', {
            time: anchorLabel(note.anchor_sec),
          })}
        >
          <Anchor size={11} /> {anchorLabel(note.anchor_sec)}
        </button>
      )}

      <AttachmentView attachments={note.attachments} />
    </div>
  );
};
