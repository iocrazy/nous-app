// New notes-first Inspiration workspace (spec §2, mockup v7). Rendered by
// TopicInspirationPage when VITE_FEATURE_INSPIRATION_NOTES is on; hotspot
// integration (tab, side panel, save-as-note) arrives in P3.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, X } from 'lucide-react';
import { useToast } from '../components/Toast';
import { Composer } from '../components/Inspiration/Composer';
import { NoteTimeline } from '../components/Inspiration/NoteTimeline';
import { ActivityPanel } from '../components/Inspiration/ActivityPanel';
import { TagsPanel } from '../components/Inspiration/TagsPanel';
import {
  deleteNote,
  getTagCounts,
  listNotes,
  updateNote,
  type InspirationNote,
} from '../services/inspirationService';

const PAGE_SIZE = 50;

export const InspirationPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [notes, setNotes] = useState<InspirationNote[]>([]);
  const [tags, setTags] = useState<{ tag: string; cnt: number }[]>([]);
  const [date, setDate] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [queryInput, setQueryInput] = useState('');
  const [q, setQ] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [editing, setEditing] = useState<InspirationNote | null>(null);
  const [editText, setEditText] = useState('');
  // Monotonic request version: guards both the main filter-driven fetch and
  // loadMore against applying a stale response after filters (date/tag/q)
  // change mid-flight (see task-7 review finding).
  const requestSeq = useRef(0);

  useEffect(() => {
    const id = setTimeout(() => setQ(queryInput.trim()), 300);
    return () => clearTimeout(id);
  }, [queryInput]);

  const filters = useMemo(
    () => ({ date: date ?? undefined, tag: tag ?? undefined, q: q || undefined }),
    [date, tag, q],
  );

  useEffect(() => {
    let alive = true;
    const seq = ++requestSeq.current;
    (async () => {
      setLoading(true);
      try {
        const rows = await listNotes(filters, PAGE_SIZE, undefined);
        if (!alive || seq !== requestSeq.current) return;
        setNotes(rows);
        setHasMore(rows.length === PAGE_SIZE);
      } catch (err) {
        if (alive) addToast(`Failed to load notes: ${(err as Error).message}`, 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // addToast is context-stable (Toast provider useCallback)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, refreshKey]);

  useEffect(() => {
    let alive = true;
    getTagCounts()
      .then((rows) => alive && setTags(rows))
      .catch((err) => console.error('tag counts failed', err));
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  const loadMore = useCallback(async () => {
    if (!notes.length || loading) return;
    const seq = requestSeq.current;
    setLoading(true);
    try {
      const rows = await listNotes(filters, PAGE_SIZE, notes[notes.length - 1].id);
      if (seq !== requestSeq.current) return; // filters changed while in-flight; discard
      setNotes((prev) => [...prev, ...rows]);
      setHasMore(rows.length === PAGE_SIZE);
    } catch (err) {
      addToast(`Failed to load notes: ${(err as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notes, loading, filters]);

  const onCreated = (note: InspirationNote) => {
    setNotes((prev) => [note, ...prev]);
    setRefreshKey((k) => k + 1);
  };

  const onTogglePin = async (note: InspirationNote) => {
    try {
      const updated = await updateNote(note.id, { pinned: !note.pinned });
      setNotes((prev) => prev.map((n) => (n.id === note.id ? updated : n)));
    } catch (err) {
      addToast((err as Error).message, 'error');
    }
  };

  const onDelete = async (note: InspirationNote) => {
    if (!window.confirm(t('inspiration.deleteConfirm', 'Delete this note?'))) return;
    try {
      await deleteNote(note.id);
      setNotes((prev) => prev.filter((n) => n.id !== note.id));
      setRefreshKey((k) => k + 1);
    } catch (err) {
      addToast((err as Error).message, 'error');
    }
  };

  const startEdit = (note: InspirationNote) => {
    setEditing(note);
    setEditText(note.content_md);
  };

  const saveEdit = async () => {
    if (!editing) return;
    try {
      const updated = await updateNote(editing.id, { content_md: editText });
      setNotes((prev) => prev.map((n) => (n.id === editing.id ? updated : n)));
      setEditing(null);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      addToast((err as Error).message, 'error');
    }
  };

  return (
    <div className="mx-auto max-w-[1180px] px-6 py-6">
      <div className="mb-3 flex items-center gap-3 rounded-xl bg-island px-4 py-2.5">
        <h2 className="text-[15px] font-semibold text-content">
          {t('inspiration.title', 'Inspiration')}
        </h2>
        {tag && (
          <button
            onClick={() => setTag(null)}
            className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/15 px-2.5 py-1 text-xs text-indigo-300"
          >
            #{tag} <X size={10} aria-label="Clear tag filter" />
          </button>
        )}
        {date && (
          <button
            onClick={() => setDate(null)}
            className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/15 px-2.5 py-1 text-xs text-indigo-300"
          >
            {date} <X size={10} aria-label="Clear date filter" />
          </button>
        )}
        <div className="ml-auto flex w-64 items-center gap-2 rounded-lg bg-island-2 px-3 py-1.5">
          <Search size={13} className="shrink-0 text-content-4" />
          <input
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            placeholder={t('inspiration.searchPlaceholder', 'Search notes…')}
            className="w-full bg-transparent text-xs text-content placeholder:text-content-4 focus:outline-none"
          />
        </div>
      </div>

      <div className="flex gap-3">
        <div className="min-w-0 flex-1 space-y-2.5">
          <Composer onCreated={onCreated} tagSuggestions={tags.map((x) => x.tag)} />
          <NoteTimeline
            notes={notes}
            onEdit={startEdit}
            onTogglePin={onTogglePin}
            onDelete={onDelete}
            onTagClick={(tg) => setTag(tg)}
            hasMore={hasMore}
            loading={loading}
            loadMore={() => void loadMore()}
          />
        </div>
        <div className="hidden w-[292px] shrink-0 space-y-2.5 lg:block">
          <ActivityPanel selectedDate={date} onSelectDate={setDate} refreshKey={refreshKey} />
          <TagsPanel tags={tags} activeTag={tag} onTagClick={setTag} />
        </div>
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-xl rounded-xl bg-island p-4">
            <h4 className="mb-2 text-sm font-semibold text-content">
              {t('inspiration.editNote', 'Edit note')}
            </h4>
            <textarea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              rows={6}
              className="w-full resize-y rounded-lg bg-island-2 p-3 text-[13.5px] text-content focus:outline-none"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button onClick={() => setEditing(null)} className="rounded-lg bg-island-2 px-4 py-1.5 text-xs text-content-2">
                {t('inspiration.cancel', 'Cancel')}
              </button>
              <button onClick={() => void saveEdit()} className="rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-semibold text-white">
                {t('inspiration.save', 'Save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
