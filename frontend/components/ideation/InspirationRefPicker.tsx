import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Hash, Lightbulb } from 'lucide-react';
import { listNotes, type InspirationNote } from '../../services/inspirationService';
import { getHotspots, type Hotspot } from '../../services/topicService';

// Minimal reference picker for the two inspiration sources — inspiration notes
// and "# topics" (the hotspots feed). No dedicated picker existed to reuse
// (only list/side-panel components), so this is the minimal list dialog the
// M1.5 spec allows. Library resources reuse chat/ResourcePicker instead.

/** A source selection normalized to what createTopic needs. */
export interface InspirationRef {
  kind: 'inspiration' | 'topic';
  title: string;
  excerpt?: string | null;
  cover_url?: string | null;
  note_id?: string;
  inspiration_topic_id?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSelect: (ref: InspirationRef) => void;
}

type Tab = 'notes' | 'topics';

/** First non-empty line of a note's markdown, trimmed for a card title. */
function noteTitle(md: string): string {
  const line = (md || '').split('\n').map((l) => l.trim()).find(Boolean) ?? '';
  const stripped = line.replace(/^#+\s*/, '').trim();
  return stripped.slice(0, 120) || 'Untitled note';
}

export default function InspirationRefPicker({ open, onClose, onSelect }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('notes');
  const [notes, setNotes] = useState<InspirationNote[]>([]);
  const [topics, setTopics] = useState<Hotspot[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    const load = tab === 'notes' ? listNotes({}, 50) : getHotspots();
    Promise.resolve(load)
      .then((rows) => {
        if (cancelled) return;
        if (tab === 'notes') setNotes(rows as InspirationNote[]);
        else setTopics(rows as Hotspot[]);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        console.error('[InspirationRefPicker] load failed:', err);
        if (tab === 'notes') setNotes([]);
        else setTopics([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, tab]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const pickNote = (n: InspirationNote) => {
    onSelect({
      kind: 'inspiration',
      title: noteTitle(n.content_md),
      excerpt: n.content_md.slice(0, 240) || null,
      note_id: n.id,
    });
    onClose();
  };

  const pickTopic = (h: Hotspot) => {
    onSelect({
      kind: 'topic',
      title: h.title,
      excerpt: h.summary || h.ai_summary || null,
      cover_url: h.cover_url ?? null,
      inspiration_topic_id: h.id,
    });
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-[2px]"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      data-testid="inspiration-ref-picker"
    >
      <div className="flex max-h-[80vh] w-[520px] flex-col overflow-hidden rounded-2xl border border-ink-800 bg-ink-900 shadow-2xl">
        {/* Header + tabs */}
        <div className="flex items-center justify-between border-b border-ink-800 px-5 py-4">
          <div className="flex gap-1">
            <button
              onClick={() => setTab('notes')}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] transition-colors ${
                tab === 'notes'
                  ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                  : 'text-ink-400 hover:text-ink-200'
              }`}
            >
              <Lightbulb size={14} />
              {t('projects.ideation.picker.notes')}
            </button>
            <button
              onClick={() => setTab('topics')}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] transition-colors ${
                tab === 'topics'
                  ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                  : 'text-ink-400 hover:text-ink-200'
              }`}
            >
              <Hash size={14} />
              {t('projects.ideation.picker.topics')}
            </button>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-ink-400 transition-colors hover:bg-ink-800 hover:text-ink-100"
            aria-label={t('common.cancel')}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {loading ? (
            <div className="py-10 text-center text-[13px] text-ink-500">
              {t('common.loading', 'Loading...')}
            </div>
          ) : tab === 'notes' ? (
            notes.length === 0 ? (
              <div className="py-10 text-center text-[13px] text-ink-500">
                {t('projects.ideation.picker.empty')}
              </div>
            ) : (
              <ul className="flex flex-col gap-1">
                {notes.map((n) => (
                  <li key={n.id}>
                    <button
                      onClick={() => pickNote(n)}
                      className="w-full rounded-lg border border-transparent px-3 py-2 text-left transition-colors hover:border-ink-700 hover:bg-ink-800/50"
                    >
                      <p className="truncate text-sm text-ink-100">{noteTitle(n.content_md)}</p>
                      {n.tags.length > 0 && (
                        <p className="mt-0.5 truncate text-[11px] text-ink-500">
                          {n.tags.map((tg) => `#${tg}`).join(' ')}
                        </p>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )
          ) : topics.length === 0 ? (
            <div className="py-10 text-center text-[13px] text-ink-500">
              {t('projects.ideation.picker.empty')}
            </div>
          ) : (
            <ul className="flex flex-col gap-1">
              {topics.map((h) => (
                <li key={h.id}>
                  <button
                    onClick={() => pickTopic(h)}
                    className="flex w-full items-center gap-3 rounded-lg border border-transparent px-3 py-2 text-left transition-colors hover:border-ink-700 hover:bg-ink-800/50"
                  >
                    <Hash size={14} className="shrink-0 text-ink-500" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-ink-100">{h.title}</span>
                      {h.source_label && (
                        <span className="block truncate text-[11px] text-ink-500">
                          {h.source_label}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
