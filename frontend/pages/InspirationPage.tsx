// New notes-first Inspiration workspace (spec §2, mockup v7). Rendered by
// TopicInspirationPage when VITE_FEATURE_INSPIRATION_NOTES is on. P3 adds the
// Notes/Hotspots tabs, the save-as-note loop and a global Parse entry point.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, Link2, Search, X } from 'lucide-react';
import { useToast } from '../components/Toast';
import { Composer } from '../components/Inspiration/Composer';
import { ApiTokensPanel } from '../components/Inspiration/ApiTokensPanel';
import { NoteTimeline } from '../components/Inspiration/NoteTimeline';
import { ActivityPanel } from '../components/Inspiration/ActivityPanel';
import { TagsPanel } from '../components/Inspiration/TagsPanel';
import { HotspotsWorkspace } from '../components/Inspiration/HotspotsWorkspace';
import { HotspotsSidePanel } from '../components/Inspiration/HotspotsSidePanel';
import { useHotspots } from '../components/Inspiration/useHotspots';
import { buildPrefillContent, hotspotToRef } from '../components/Inspiration/hotspotToRef';
import { toggleTaskItem } from '../components/Inspiration/toggleTaskItem';
import { FloatingParse } from '../components/TopicInspiration/FloatingParse';
import type { Hotspot } from '../services/topicService';
import {
  deleteNote,
  getTagCounts,
  listNotes,
  updateNote,
  type InspirationNote,
  type NoteAttachment,
  type RefHotspot,
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
  const [tab, setTab] = useState<'notes' | 'hotspots'>('notes');
  const [prefill, setPrefill] = useState<{ content: string; refHotspot: RefHotspot } | null>(null);
  const [prefillNonce, setPrefillNonce] = useState(0);
  const [parseOpen, setParseOpen] = useState(false);
  const [parseUrl, setParseUrl] = useState<string | null>(null);
  const [tokensOpen, setTokensOpen] = useState(false);
  // Single page-wide hotspots instance (task-3): the Hotspots-tab workspace,
  // the Notes-tab side panel and the category chips all read off this one
  // fetch, so hiding a hotspot (applyState) is instantly consistent
  // everywhere instead of each component holding its own stale copy.
  const { hotspots, loading: hotspotsLoading, applyState } = useHotspots({
    enabled: true,
    day: date ?? undefined,
  });
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  // Monotonic request version: guards both the main filter-driven fetch and
  // loadMore against applying a stale response after filters (date/tag/q)
  // change mid-flight (see task-7 review finding).
  const requestSeq = useRef(0);
  // Per-note in-flight write sequence for checkbox toggles (task-3 review
  // finding): rapid clicks on the same note's checkboxes fire concurrent
  // `updateNote` PATCHes whose responses can land out of order. Keyed by
  // note.id so unrelated notes never block each other; the latest seq for a
  // given note is the only response allowed to apply/revert its content_md.
  const toggleSeq = useRef<Record<string, number>>({});

  useEffect(() => {
    const id = setTimeout(() => setQ(queryInput.trim()), 300);
    return () => clearTimeout(id);
  }, [queryInput]);

  // Category chips are per-day: switching the day resets the filter so a
  // vanished chip can't leave an invisible, unclearable filter behind.
  useEffect(() => {
    setActiveCategory(null);
  }, [date]);

  const filters = useMemo(
    () => ({ date: date ?? undefined, tag: tag ?? undefined, q: q || undefined }),
    [date, tag, q],
  );

  const hotspotCategories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const h of hotspots) {
      if (!h.category) continue;
      counts.set(h.category, (counts.get(h.category) ?? 0) + 1);
    }
    return Array.from(counts.entries()).map(([category, cnt]) => ({ category, cnt }));
  }, [hotspots]);

  // Clicking the active category again clears the filter.
  const onCategoryClick = (category: string) => {
    setActiveCategory((prev) => (prev === category ? null : category));
  };

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

  // Save-as-note loop (spec §2 #6): snapshot the hotspot into a ref, hand it
  // to the Composer as a prefill, and jump to the Notes tab. Shared by the
  // Hotspots-tab workspace and the Notes-tab sidebar Top3.
  const handleSaveAsNote = (h: Hotspot) => {
    setPrefill({ content: buildPrefillContent(h), refHotspot: hotspotToRef(h) });
    setPrefillNonce((n) => n + 1);
    setTab('notes');
  };

  // A Composer retry succeeded after the note was already created/rendered —
  // merge the attachment into that note's card in place.
  const onAttachmentUploaded = (noteId: string, attachment: NoteAttachment) => {
    setNotes((prev) =>
      prev.map((n) =>
        n.id === noteId ? { ...n, attachments: [...n.attachments, attachment] } : n,
      ),
    );
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

  const onToggleTask = async (note: InspirationNote, index: number) => {
    const nextMd = toggleTaskItem(note.content_md, index);
    if (nextMd === note.content_md) return;
    // Claim this note's latest write slot *before* the optimistic update so
    // a response that lands after a newer toggle (of the same note) never
    // clobbers state it's no longer authoritative for.
    const seq = (toggleSeq.current[note.id] ?? 0) + 1;
    toggleSeq.current[note.id] = seq;
    setNotes((prev) => prev.map((n) => (n.id === note.id ? { ...n, content_md: nextMd } : n)));
    try {
      const updated = await updateNote(note.id, { content_md: nextMd });
      if (seq !== toggleSeq.current[note.id]) return; // superseded by a newer toggle; discard
      setNotes((prev) => prev.map((n) => (n.id === note.id ? updated : n)));
    } catch (err) {
      if (seq === toggleSeq.current[note.id]) {
        setNotes((prev) => prev.map((n) => (n.id === note.id ? note : n)));
      }
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
        <div className="flex items-center gap-0.5 rounded-lg bg-island-2 p-0.5">
          <button
            onClick={() => setTab('notes')}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
              tab === 'notes' ? 'bg-island text-content shadow-sm' : 'text-content-3 hover:text-content-2'
            }`}
          >
            {t('inspiration.notes', 'Notes')}
          </button>
          <button
            onClick={() => setTab('hotspots')}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
              tab === 'hotspots' ? 'bg-island text-content shadow-sm' : 'text-content-3 hover:text-content-2'
            }`}
          >
            {t('inspiration.hotspotsTab', 'Hotspots')}
          </button>
        </div>
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
        <button
          onClick={() => {
            setParseUrl(null);
            setParseOpen(true);
          }}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-indigo-500/15 px-3 py-1.5 text-xs font-semibold text-indigo-300 hover:bg-indigo-500/25"
        >
          <Link2 size={13} />
          {t('inspiration.parseUrl', 'Parse URL')}
        </button>
        <button
          onClick={() => setTokensOpen(true)}
          title={t('inspiration.tokens.title', 'API Tokens')}
          aria-label={t('inspiration.tokens.title', 'API Tokens')}
          className="inline-flex shrink-0 items-center justify-center rounded-lg bg-island-2 p-1.5 text-content-3 hover:bg-line hover:text-content-2"
        >
          <KeyRound size={15} />
        </button>
      </div>

      <div className="flex gap-3">
        <div className="min-w-0 flex-1 space-y-2.5">
          {tab === 'notes' ? (
            <>
              <Composer
                key={prefillNonce}
                prefill={prefill}
                autoFocus={!!prefill}
                onCreated={(note) => {
                  onCreated(note);
                  setPrefill(null);
                }}
                onAttachmentUploaded={onAttachmentUploaded}
                tagSuggestions={tags.map((x) => x.tag)}
              />
              <NoteTimeline
                notes={notes}
                onEdit={startEdit}
                onTogglePin={onTogglePin}
                onDelete={onDelete}
                onTagClick={(tg) => setTag(tg)}
                onToggleTask={(note, index) => void onToggleTask(note, index)}
                hasMore={hasMore}
                loading={loading}
                loadMore={() => void loadMore()}
              />
            </>
          ) : (
            <HotspotsWorkspace
              hotspots={hotspots}
              loading={hotspotsLoading}
              applyState={applyState}
              activeCategory={activeCategory}
              onSaveAsNote={handleSaveAsNote}
              onParse={(h) => {
                setParseUrl(h.origin_url || h.url || null);
                setParseOpen(true);
              }}
            />
          )}
        </div>
        <div className="hidden w-[292px] shrink-0 space-y-2.5 lg:block">
          <ActivityPanel selectedDate={date} onSelectDate={setDate} refreshKey={refreshKey} />
          {tab === 'notes' ? (
            <>
              <HotspotsSidePanel
                hotspots={hotspots}
                day={date}
                onSaveAsNote={handleSaveAsNote}
                onOpenAll={() => setTab('hotspots')}
              />
              <TagsPanel tags={tags} activeTag={tag} onTagClick={setTag} />
            </>
          ) : (
            hotspotCategories.length > 0 && (
              <div className="rounded-xl bg-island px-4 py-3.5">
                <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
                  {t('inspiration.hotspots', 'Hotspots')}
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {hotspotCategories.map(({ category, cnt }) => (
                    <button
                      key={category}
                      onClick={() => onCategoryClick(category)}
                      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors ${
                        activeCategory === category
                          ? 'bg-indigo-500/25 text-indigo-300'
                          : 'bg-island-2 text-content-2 hover:bg-line'
                      }`}
                    >
                      {`#${category} (${cnt})`}
                    </button>
                  ))}
                </div>
              </div>
            )
          )}
        </div>
      </div>

      <FloatingParse open={parseOpen} onOpenChange={setParseOpen} initialUrl={parseUrl ?? undefined} />

      {tokensOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setTokensOpen(false)}
        >
          <div
            className="w-full max-w-lg rounded-xl bg-island p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center gap-2">
              <KeyRound size={15} className="text-indigo-300" />
              <h4 className="text-sm font-semibold text-content">
                {t('inspiration.tokens.title', 'API Tokens')}
              </h4>
              <button
                onClick={() => setTokensOpen(false)}
                aria-label={t('inspiration.tokens.close', 'Close')}
                className="ml-auto rounded p-1 text-content-3 hover:bg-island-2 hover:text-content-2"
              >
                <X size={15} />
              </button>
            </div>
            <ApiTokensPanel />
          </div>
        </div>
      )}

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
