// frontend/components/DownloadsView/TagFilterSection.tsx
//
// Searchable tag picker for the mobile filter sheet. A flat wall of tag pills
// doesn't scale as the tag set grows, so this applies the standard large-facet
// pattern: selected tags pinned on top (removable), a search box (type-to-
// filter), and — when not searching — only the most-used top-N by default with
// a "Show all" affordance. Frequency comes from the tag's own media_count /
// video_count (already on the Tag row), so no extra request.
//
// Client-side filtering is fine into the hundreds. When the catalog reaches the
// thousands, swap the in-memory filter for a debounced server-side typeahead
// endpoint + list virtualization (the component boundary keeps that contained).

import { useMemo, useState } from 'react';
import { Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Tag } from '../../types';
import { Pill } from './filterSheetUi';

const DEFAULT_VISIBLE = 14;

function tagCount(t: Tag): number {
  return t.media_count ?? t.video_count ?? 0;
}

function byCountDesc(a: Tag, b: Tag): number {
  const d = tagCount(b) - tagCount(a);
  return d !== 0 ? d : a.name.localeCompare(b.name);
}

function matches(t: Tag, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return (
    t.name.toLowerCase().includes(needle) ||
    (t.name_zh ?? '').toLowerCase().includes(needle)
  );
}

export function TagFilterSection({
  allTags,
  selectedIds,
  onToggle,
  onClear,
}: {
  allTags: Tag[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const tagById = useMemo(() => {
    const m = new Map<string, Tag>();
    for (const tag of allTags) m.set(tag.id, tag);
    return m;
  }, [allTags]);

  // Selected tags shown as chips up top — resolve ids → Tag (skip unknowns).
  const selectedTags = useMemo(
    () => selectedIds.map((id) => tagById.get(id)).filter((x): x is Tag => !!x),
    [selectedIds, tagById],
  );

  // Candidate list = not-yet-selected, search-filtered, frequency-sorted.
  const candidates = useMemo(() => {
    const list = allTags
      .filter((tag) => !selectedSet.has(tag.id) && matches(tag, query))
      .sort(byCountDesc);
    return list;
  }, [allTags, selectedSet, query]);

  const searching = query.trim().length > 0;
  const visible = searching || showAll ? candidates : candidates.slice(0, DEFAULT_VISIBLE);
  const hiddenCount = candidates.length - visible.length;

  return (
    <div className="space-y-3">
      {/* Selected chips */}
      {selectedTags.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selectedTags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              onClick={() => onToggle(tag.id)}
              className="inline-flex items-center gap-1 pl-3 pr-2 py-1.5 rounded-full text-xs font-medium bg-indigo-500 border border-indigo-400 text-white"
            >
              {tag.name}
              <X size={12} className="opacity-80" />
            </button>
          ))}
          <button
            type="button"
            onClick={onClear}
            className="text-[11px] text-ink-400 active:text-ink-50 px-1"
          >
            {t('resources.filter.clear', 'Clear')}
          </button>
        </div>
      )}

      {/* Search box */}
      <div className="flex items-center gap-2 bg-ink-800 border border-ink-700 rounded-lg px-3 py-2">
        <Search size={15} className="text-ink-500 shrink-0" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('resources.filter.searchTags', 'Search tags…')}
          className="flex-1 min-w-0 bg-transparent outline-none text-sm text-ink-50 placeholder-ink-500"
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery('')}
            aria-label={t('common.clear', 'Clear')}
            className="text-ink-500 active:text-ink-50"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Candidate pills */}
      {visible.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {visible.map((tag) => (
            <Pill key={tag.id} active={false} onClick={() => onToggle(tag.id)}>
              {tag.name}
            </Pill>
          ))}
        </div>
      ) : (
        <div className="text-xs text-ink-500 py-1">
          {t('resources.filter.noTags', 'No matching tags')}
        </div>
      )}

      {/* Show all / less — only when not searching and there's a tail */}
      {!searching && (hiddenCount > 0 || showAll) && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="text-xs font-medium text-indigo-400 active:text-indigo-300"
        >
          {showAll
            ? t('resources.filter.showLess', 'Show less')
            : t('resources.filter.showAllN', 'Show all ({{n}})', {
                n: candidates.length,
              })}
        </button>
      )}
    </div>
  );
}
