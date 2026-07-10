import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import type { InspirationNote } from '../../services/inspirationService';

interface Props {
  recentNotes: InspirationNote[];
  onQuickSave: (content: string) => Promise<void>;
  onOpenNotes: () => void;
}

function firstLine(md: string): string {
  return md.split('\n', 1)[0];
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const NotesSidePanel: React.FC<Props> = ({ recentNotes, onQuickSave, onOpenNotes }) => {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const content = text.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      await onQuickSave(content);
      setText('');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
        <Lightbulb size={12} />
        {t('inspiration.notesPanel', 'Notes')}
      </h3>
      <div className="flex items-center gap-1.5 rounded-lg bg-island-2 py-1 pl-3 pr-1">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void submit()}
          placeholder={t('inspiration.quickNote', 'Quick note…')}
          className="w-full bg-transparent text-xs text-content placeholder:text-content-4 focus:outline-none"
        />
        <button
          onClick={() => void submit()}
          disabled={saving || !text.trim()}
          className="shrink-0 rounded-md bg-indigo-500 px-2.5 py-1 text-[10.5px] font-semibold text-white disabled:opacity-40"
        >
          {t('inspiration.save', 'Save')}
        </button>
      </div>
      {recentNotes.slice(0, 3).map((n) => (
        <div key={n.id} className="border-t border-line py-2 first-of-type:mt-2">
          <div className="truncate text-[12px] leading-snug text-content">{firstLine(n.content_md)}</div>
          <div className="mt-0.5 flex gap-2 text-[10px] text-content-4 tabular-nums">
            <span>{timeOf(n.created_at)}</span>
            {n.attachments.length > 0 && (
              <span>{t('inspiration.fileCount', '{{count}} files', { count: n.attachments.length })}</span>
            )}
          </div>
        </div>
      ))}
      <div className="mt-2 border-t border-line pt-2.5">
        <button onClick={onOpenNotes} className="text-[11.5px] text-content-3 hover:text-content-2">
          {t('inspiration.openInNotes', 'Open in Notes →')}
        </button>
      </div>
    </div>
  );
};
