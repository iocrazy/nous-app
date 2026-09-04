// New notes-first Inspiration workspace (spec §2, mockup v7). Rendered by
// TopicInspirationPage when VITE_FEATURE_INSPIRATION_NOTES is on. P3 adds the
// Notes/Hotspots tabs, the save-as-note loop and a global Parse entry point.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link2, Search, Star, Tag as TagIcon, X } from 'lucide-react';
import { useToast } from '../components/Toast';
import { Composer } from '../components/Inspiration/Composer';
import { NoteTimeline } from '../components/Inspiration/NoteTimeline';
import { ActivityPanel } from '../components/Inspiration/ActivityPanel';
import { TagsPanel } from '../components/Inspiration/TagsPanel';
import { HotspotsWorkspace } from '../components/Inspiration/HotspotsWorkspace';
import { HotspotsSidePanel } from '../components/Inspiration/HotspotsSidePanel';
import { NotesSidePanel } from '../components/Inspiration/NotesSidePanel';
import { useHotspots } from '../components/Inspiration/useHotspots';
import { buildPrefillContent, hotspotToRef } from '../components/Inspiration/hotspotToRef';
import { toggleTaskItem } from '../components/Inspiration/toggleTaskItem';
import { FloatingParse } from '../components/TopicInspiration/FloatingParse';
import type { Hotspot } from '../services/topicService';
import {
  createNote,
  deleteNote,
  getTagCounts,
  listNotes,
  updateNote,
  type InspirationNote,
  type NoteAttachment,
  type RefHotspot,
} from '../services/inspirationService';
import { fetchAllTags } from '../services/unifiedTagService';
import { PageHeader } from '../components/layout/PageHeader';
// Reused verbatim from the Resources filter bar — both are plain controlled
// components with no ResourcesContext dependency.
import { FilterChip } from '../components/resources/filter/FilterChip';
import { RatingFilterDropdown } from '../components/resources/filter/RatingFilterDropdown';
import { NoteTagsFilterDropdown } from '../components/Inspiration/NoteTagsFilterDropdown';
import type { Tag } from '../types';

const PAGE_SIZE = 50;

/**
 * Suggestions for the note `#` completion menu: curated pool tags first
 * (each with its bilingual name_zh alias), then the user's own note history.
 * Shadow pool tags (origin === 'note') are skipped since they duplicate note history.
 */
export function buildTagSuggestions(
  noteTags: { tag: string; cnt: number }[],
  poolTags: Tag[],
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const push = (w: string | null | undefined) => {
    const v = (w || '').trim();
    if (!v || seen.has(v.toLowerCase())) return;
    seen.add(v.toLowerCase());
    out.push(v);
  };
  for (const t of poolTags) {
    if (t.origin === 'note') continue;
    push(t.name);
    push(t.name_zh);
  }
  for (const n of noteTags) push(n.tag);
  return out;
}

export const InspirationPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [notes, setNotes] = useState<InspirationNote[]>([]);
  const [tags, setTags] = useState<{ tag: string; cnt: number }[]>([]);
  const [poolTags, setPoolTags] = useState<Tag[]>([]);
  const [date, setDate] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  // 0 = "Any rating" (no filter); 1-5 = minimum stars.
  const [minRating, setMinRating] = useState(0);
  const [ratingChipOpen, setRatingChipOpen] = useState(false);
  const [tagChipOpen, setTagChipOpen] = useState(false);
  const [queryInput, setQueryInput] = useState('');
  const [q, setQ] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [editing, setEditing] = useState<InspirationNote | null>(null);
  const [tab, setTab] = useState<'notes' | 'hotspots'>('notes');
  const [prefill, setPrefill] = useState<{ content: string; refHotspot: RefHotspot } | null>(null);
  const [prefillNonce, setPrefillNonce] = useState(0);
  const [parseOpen, setParseOpen] = useState(false);
  const [parseUrl, setParseUrl] = useState<string | null>(null);
  // Hotspots-tab tag narrowing (§7.4): multi-select over the visible pool tags,
  // threaded server-side through useHotspots → getHotspots(tag_id=…).
  const [tagFilterIds, setTagFilterIds] = useState<string[]>([]);
  // Single page-wide hotspots instance (task-3): the Hotspots-tab workspace,
  // the Notes-tab side panel and the category chips all read off this one
  // fetch, so hiding a hotspot (applyState) is instantly consistent
  // everywhere instead of each component holding its own stale copy.
  const { hotspots, loading: hotspotsLoading, applyState } = useHotspots({
    enabled: true,
    day: date ?? undefined,
    tagIds: tagFilterIds,
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
  const ratingSeq = useRef<Record<string, number>>({});
  // The edit modal's own element — focus target and Tab-trap boundary.
  const editDialogRef = useRef<HTMLDivElement>(null);

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
    () => ({
      date: date ?? undefined,
      tag: tag ?? undefined,
      q: q || undefined,
      // `|| undefined`, not the raw 0: "Any rating" must drop the param
      // rather than ask for `rating >= 0`.
      min_rating: minRating || undefined,
    }),
    [date, tag, q, minRating],
  );

  // Autocomplete suggestions shared by the Composer and the edit NoteEditor —
  // memoized so both consumers get one stable array per (tags, poolTags).
  const tagSuggestions = useMemo(() => buildTagSuggestions(tags, poolTags), [tags, poolTags]);

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

  // Hotspot tag-filter options: the visible pool tags minus shadow rows
  // (origin === 'note'), which duplicate note history and aren't curated
  // narrowing dimensions.
  const tagFilterOptions = useMemo(
    () => poolTags.filter((tg) => tg.origin !== 'note'),
    [poolTags],
  );

  const onTagFilterToggle = (id: string) => {
    setTagFilterIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
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

  useEffect(() => {
    fetchAllTags()
      .then(setPoolTags)
      .catch((err) => console.error('fetchAllTags failed', err));
  }, []);

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

  const onRating = async (note: InspirationNote, value: number) => {
    // Currently UNREACHABLE: RatingStars maps a click on the already-selected
    // star to 0, so the value it emits never equals the current one. Kept as a
    // symmetric no-op guard (onToggleTask has the same early return, and that
    // one IS reachable) and as cover if that contract ever changes. No test
    // pins this line — do not read it as covered behaviour.
    if ((note.rating ?? 0) === value) return;
    // Same seq-guard as onToggleTask: claim this note's latest write slot
    // BEFORE the optimistic update, so a response that lands after a newer
    // click (of the same note) never clobbers state it no longer owns.
    const seq = (ratingSeq.current[note.id] ?? 0) + 1;
    ratingSeq.current[note.id] = seq;
    setNotes((prev) => prev.map((n) => (n.id === note.id ? { ...n, rating: value } : n)));
    try {
      const updated = await updateNote(note.id, { rating: value });
      if (seq !== ratingSeq.current[note.id]) return; // superseded; discard
      // The rating is a LIST PREDICATE now (the Rating chip filters on it), so
      // a note rated below the active floor has to leave the list — otherwise
      // a "≥4★" list keeps showing the card the user just dropped to 2 stars,
      // which reads as the filter lying. Dropped locally rather than via
      // setRefreshKey so pages already pulled by loadMore survive, and only on
      // the CONFIRMED value — the catch below reverts by id and cannot
      // re-insert a card that is already gone.
      // `minRating === 0` needs no special case: ratings are 0-5, so `>= 0`
      // already keeps everything.
      const stillMatches = (updated.rating ?? 0) >= minRating;
      setNotes((prev) =>
        stillMatches
          ? prev.map((n) => (n.id === note.id ? updated : n))
          : prev.filter((n) => n.id !== note.id),
      );
    } catch (err) {
      if (seq === ratingSeq.current[note.id]) {
        setNotes((prev) => prev.map((n) => (n.id === note.id ? note : n)));
      }
      addToast((err as Error).message, 'error');
    }
  };

  const startEdit = (note: InspirationNote) => setEditing(note);

  /**
   * Modal keyboard + focus behaviour. `aria-modal="true"` tells assistive tech
   * that everything outside the dialog is inert right now — leaving focus on
   * the page behind would make that a false claim, so this moves focus in,
   * keeps Tab inside, hands focus back on close, and closes on Escape.
   */
  useEffect(() => {
    if (!editing) return;
    const dialog = editDialogRef.current;
    const restoreTo = document.activeElement as HTMLElement | null;
    dialog?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      // The attachment lightbox stacks above this modal (z-100 vs z-50) and
      // listens on window too, so a keypress reaches BOTH handlers. Whoever is
      // on top wins: while the lightbox is mounted this modal keeps its hands
      // off — Escape closes the lightbox, not us, and Tab must not yank focus
      // out of the layer above and down into the editor underneath it.
      //
      // ⚠️ This relies on the lightbox being a bare conditional render with no
      // exit animation (AttachmentView: `{lightbox && <ImageLightbox/>}`), so
      // it leaves the DOM immediately. Give it a leave transition and this
      // guard starts swallowing Escape for the duration of that animation —
      // the modal would feel like it ignores the key.
      if (document.querySelector('[data-lightbox="attachment"]')) return;
      if (e.key === 'Escape') {
        setEditing(null);
        return;
      }
      if (e.key !== 'Tab' || !dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), textarea, select, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]',
        ),
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const outside = !dialog.contains(active);
      // `active === dialog` matters for the very first Shift+Tab: focus starts
      // on the dialog container itself (tabIndex={-1}), which is neither
      // "outside" nor the first focusable — without this it walks backwards
      // straight out of the modal.
      if (
        e.shiftKey
          ? outside || active === first || active === dialog
          : outside || active === last
      ) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      // Only if it survived the modal — the "Edit" menu item that opened this
      // usually did not.
      if (restoreTo && document.contains(restoreTo)) restoreTo.focus();
    };
  }, [editing]);

  /**
   * The edit modal's Composer calls this LAST, after it has applied its own
   * attachment adds/removals — so `updated.attachments` is already the
   * post-save list and can be stored verbatim.
   *
   * Deliberately does NOT catch: the Composer stays mounted, toasts the
   * reason and keeps the user's text. Swallowing the error here would close
   * nothing, report nothing, and look like a successful save.
   */
  const saveEdit = async (id: string, content: string): Promise<InspirationNote> => {
    const updated = await updateNote(id, { content_md: content });
    setNotes((prev) => prev.map((n) => (n.id === id ? updated : n)));
    setEditing(null);
    setRefreshKey((k) => k + 1);
    return updated;
  };

  // Symmetric with onAttachmentUploaded: a deletion that already landed on
  // the server must leave the card immediately, even if a later step of the
  // same save fails and the modal stays open.
  const onAttachmentDeleted = (noteId: string, attachmentId: string) => {
    setNotes((prev) =>
      prev.map((n) =>
        n.id === noteId
          ? { ...n, attachments: n.attachments.filter((a) => a.id !== attachmentId) }
          : n,
      ),
    );
  };

  return (
    <div className="mx-auto w-full max-w-[1800px] px-6 py-6 2xl:px-10">
      <PageHeader
        className="mb-3"
        // No secondary rail in this module, so this IS the module name.
        level="module"
        title={t('inspiration.title', 'Inspiration')}
        // Verbs only. Filters get their own row below — see the comment on it.
        actions={
          <>
            <div className="flex w-64 items-center gap-2 rounded-lg bg-island-2 px-3 py-1.5">
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
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-[var(--accent-soft)] px-3 py-1.5 text-xs font-semibold text-[var(--accent-text)] hover:bg-[var(--accent-soft)]"
            >
              <Link2 size={13} />
              {t('inspiration.parseUrl', 'Parse URL')}
            </button>
          </>
        }
        tabs={
          <>
            <button
              onClick={() => setTab('notes')}
              className={
                tab === 'notes'
                  ? 'border-b-2 border-[var(--accent-text)] px-3 pb-2 font-medium text-ink-100'
                  : 'px-3 pb-2 text-ink-500 hover:text-ink-300'
              }
            >
              {t('inspiration.notes', 'Notes')}
            </button>
            <button
              onClick={() => setTab('hotspots')}
              className={
                tab === 'hotspots'
                  ? 'border-b-2 border-[var(--accent-text)] px-3 pb-2 font-medium text-ink-100'
                  : 'px-3 pb-2 text-ink-500 hover:text-ink-300'
              }
            >
              {t('inspiration.hotspotsTab', 'Hotspots')}
            </button>
          </>
        }
      />

      {/* Filter row — same shape as the Resources bar
          (`resources/filter/FilterBar.tsx`): its own wrapping line under the
          header, never inside `actions`. That slot is right-aligned and
          `shrink-0`; filters dropped into it crowd the search box and the
          Rating chip ends up stranded mid-header. */}
      <div className="mb-3 flex items-center gap-1.5 flex-wrap">
        {/* Single-select: the backend takes one optional `tag`. This chip
            replaced the read-only "#tag ×" pill that used to sit here — the
            pill could only clear a tag chosen from the sidebar list, and
            keeping both would have shown the active tag twice. */}
        <FilterChip
          chipId="note_tags"
          label={t('inspiration.tags', 'Tags')}
          activeSummary={tag ? `#${tag}` : null}
          isActive={!!tag}
          isOpen={tagChipOpen}
          onToggle={() => setTagChipOpen((v) => !v)}
          onClose={() => setTagChipOpen(false)}
          onClear={() => setTag(null)}
          icon={TagIcon}
        >
          <NoteTagsFilterDropdown
            tags={tags}
            activeTag={tag}
            onChange={(next) => {
              setTag(next);
              setTagChipOpen(false);
            }}
          />
        </FilterChip>
        {date && (
          <button
            onClick={() => setDate(null)}
            className="inline-flex items-center gap-1.5 rounded-full bg-[var(--accent-soft)] px-2.5 py-1 text-xs text-[var(--accent-text)]"
          >
            {date} <X size={10} aria-label="Clear date filter" />
          </button>
        )}
        <FilterChip
          chipId="rating"
          label={t('inspiration.rating', 'Rating')}
          activeSummary={minRating > 0 ? `≥${minRating}★` : null}
          isActive={minRating > 0}
          isOpen={ratingChipOpen}
          onToggle={() => setRatingChipOpen((v) => !v)}
          onClose={() => setRatingChipOpen(false)}
          onClear={() => setMinRating(0)}
          icon={Star}
        >
          <RatingFilterDropdown
            minRating={minRating}
            onChange={(next) => {
              setMinRating(next);
              // Same snappy auto-close as the Resources bar's rating chip.
              if (next === 0) setRatingChipOpen(false);
            }}
          />
        </FilterChip>
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
                tagSuggestions={tagSuggestions}
              />
              <NoteTimeline
                notes={notes}
                onEdit={startEdit}
                onTogglePin={onTogglePin}
                onDelete={onDelete}
                onTagClick={(tg) => setTag(tg)}
                onToggleTask={(note, index) => void onToggleTask(note, index)}
                onRating={(note, value) => void onRating(note, value)}
                hasMore={hasMore}
                loading={loading}
                loadMore={() => void loadMore()}
                filtered={!!(date || tag || q)}
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
        <div className="hidden w-[292px] shrink-0 space-y-2.5 lg:block 2xl:w-[340px]">
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
            <>
              {hotspotCategories.length > 0 && (
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
                            ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                            : 'bg-island-2 text-content-2 hover:bg-line'
                        }`}
                      >
                        {`#${category} (${cnt})`}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {tagFilterOptions.length > 0 && (
                <div className="rounded-xl bg-island px-4 py-3.5">
                  <h3 className="mb-2.5 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-content-3">
                    <span>{t('inspiration.filterTag', 'Tag')}</span>
                    {tagFilterIds.length > 0 && (
                      <button
                        onClick={() => setTagFilterIds([])}
                        className="text-[10px] font-normal normal-case tracking-normal text-content-4 hover:text-content-2"
                      >
                        {t('inspiration.clearFilter', 'Clear')}
                      </button>
                    )}
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {tagFilterOptions.map((tg) => (
                      <button
                        key={tg.id}
                        onClick={() => onTagFilterToggle(tg.id)}
                        className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors ${
                          tagFilterIds.includes(tg.id)
                            ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                            : 'bg-island-2 text-content-2 hover:bg-line'
                        }`}
                      >
                        {tg.name_zh || tg.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <NotesSidePanel
                recentNotes={notes.slice(0, 3)}
                onQuickSave={async (content) => {
                  try {
                    const note = await createNote(content);
                    onCreated(note);
                  } catch (err) {
                    addToast((err as Error).message, 'error');
                    throw err;
                  }
                }}
                onOpenNotes={() => setTab('notes')}
              />
            </>
          )}
        </div>
      </div>

      <FloatingParse open={parseOpen} onOpenChange={setParseOpen} initialUrl={parseUrl ?? undefined} />

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          {/* max-h + overflow are load-bearing now that attachments render
              INSIDE the modal: the wrapper above is `fixed inset-0` and does
              not scroll, so without these a note with a couple of videos
              (AttachmentView renders them at max-h-64) pushes Save and the
              lower remove buttons out of the viewport, unreachable by mouse.
              tabIndex={-1} makes the dialog itself a focus target on open. */}
          <div
            ref={editDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="inspiration-edit-title"
            tabIndex={-1}
            className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-xl bg-island p-4"
          >
            <div className="mb-2 flex items-center justify-between">
              <h4 id="inspiration-edit-title" className="text-sm font-semibold text-content">
                {t('inspiration.editNote', 'Edit note')}
              </h4>
              <button
                aria-label="Cancel edit"
                title={t('inspiration.cancel', 'Cancel')}
                onClick={() => setEditing(null)}
                className="rounded p-1 text-content-3 hover:bg-island-2 hover:text-content"
              >
                <X size={15} />
              </button>
            </div>
            {/* Same component as quick capture — one editor, one toolbar, one
                attachment pipeline. `key` makes each note a fresh mount, which
                is what lets prefill / existingAttachments stay initial-value
                props instead of becoming controlled state. No autoFocus: the
                caret would land at the document START (NoteEditor's documented
                behaviour), so typing would insert before the existing text. */}
            <Composer
              key={editing.id}
              noteId={editing.id}
              prefill={{
                content: editing.content_md,
                refHotspot: editing.ref_hotspot ?? undefined,
              }}
              existingAttachments={editing.attachments}
              tagSuggestions={tagSuggestions}
              submitLabel={t('inspiration.saveChanges', 'Save Changes')}
              onSubmit={(content) => saveEdit(editing.id, content)}
              onAttachmentUploaded={onAttachmentUploaded}
              onAttachmentDeleted={onAttachmentDeleted}
            />
          </div>
        </div>
      )}
    </div>
  );
};
