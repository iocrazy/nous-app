// frontend/components/TopicInspiration/TopicFilterBar.tsx
//
// Resources-style dropdown filter bar for Topic Inspiration. One row of
// chips (View / Category / Source / Date), each opening a small dropdown.
// Reuses the Resources FilterChip shell (chip + outside-click dropdown).
// Only one chip is open at a time.

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Calendar,
  Layers,
  LayoutGrid,
  Rss,
  Tag as TagIcon,
  X,
  type LucideIcon,
} from 'lucide-react';
import { FilterChip } from '../resources/filter/FilterChip';
import {
  getSourceHealth,
  type HotspotView,
  type SourceHealth,
  type SourceHealthStatus,
} from '../../services/topicService';
import type { Tag } from '../../types';

const CATEGORIES = ['all', 'model', 'product', 'industry', 'paper', 'tips'] as const;
const VIEWS: HotspotView[] = ['all', 'featured', 'foryou', 'saved', 'hidden'];

const HEALTH_DOT: Record<SourceHealthStatus, string> = {
  ok: '#10b981',
  degraded: '#f59e0b',
  dead: '#ef4444',
};

type ChipId = 'view' | 'category' | 'source' | 'date' | 'tag';

export interface TopicFilterBarProps {
  view: HotspotView;
  onView: (v: HotspotView) => void;
  category: string;
  onCategory: (c: string) => void;
  selectedSources: string[];
  onSources: (ids: string[]) => void;
  day?: string;
  dates: string[];
  onDay: (d: string | undefined) => void;
  /** Curated pool tags (own + visible system). Shadow tags are filtered out. */
  allTags: Tag[];
  selectedTagIds: string[];
  onTagIdsChange: (ids: string[]) => void;
  /** When searching, the Date chip is meaningless (search spans all dates). */
  searching?: boolean;
}

const ROW = 'w-full text-left px-3 py-1.5 text-sm rounded-md transition-colors flex items-center gap-2';

export const TopicFilterBar: React.FC<TopicFilterBarProps> = ({
  view,
  onView,
  category,
  onCategory,
  selectedSources,
  onSources,
  day,
  dates,
  onDay,
  allTags,
  selectedTagIds,
  onTagIdsChange,
  searching = false,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState<ChipId | null>(null);
  const [sources, setSources] = useState<SourceHealth[]>([]);

  // Source list for the Source chip (visible system + own sources).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const s = await getSourceHealth();
        if (alive) setSources(s);
      } catch (err) {
        console.error('filter source list failed', err);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const ICONS: Record<ChipId, LucideIcon> = {
    view: LayoutGrid,
    category: Layers,
    source: Rss,
    date: Calendar,
    tag: TagIcon,
  };

  // Shadow tags (origin 'note') are never attachable to hotspots, so they're
  // excluded from the filter options — consistent with the resource FilterBar.
  const tagOptions = useMemo(
    () => allTags.filter((tg) => tg.origin !== 'note'),
    [allTags],
  );
  const tagName = (id: string) => {
    const tg = allTags.find((x) => String(x.id) === id);
    return tg ? tg.name_zh || tg.name : id;
  };

  const rowCls = (active: boolean) =>
    `${ROW} ${active ? 'text-accent bg-accent/10' : 'text-content-2 hover:bg-island-2'}`;

  const viewLabel = (v: HotspotView) => t(`topic.view_${v}`, v);
  const sourceName = (id: string) => sources.find((s) => s.id === id)?.name || id;

  const summaries: Record<ChipId, string | null> = {
    view: view === 'all' ? null : viewLabel(view),
    category: category === 'all' ? null : t(`topic.cat_${category}`, category),
    source:
      selectedSources.length === 0
        ? null
        : selectedSources.length === 1
          ? sourceName(selectedSources[0])
          : String(selectedSources.length),
    date: day || null,
    tag:
      selectedTagIds.length === 0
        ? null
        : selectedTagIds.length === 1
          ? tagName(selectedTagIds[0])
          : String(selectedTagIds.length),
  };

  const isActive: Record<ChipId, boolean> = {
    view: view !== 'all',
    category: category !== 'all',
    source: selectedSources.length > 0,
    date: !!day,
    tag: selectedTagIds.length > 0,
  };

  const hasAny =
    isActive.view ||
    isActive.category ||
    isActive.source ||
    isActive.date ||
    isActive.tag;

  const toggleSource = (id: string) => {
    onSources(
      selectedSources.includes(id)
        ? selectedSources.filter((x) => x !== id)
        : [...selectedSources, id],
    );
  };

  const toggleTag = (id: string) => {
    onTagIdsChange(
      selectedTagIds.includes(id)
        ? selectedTagIds.filter((x) => x !== id)
        : [...selectedTagIds, id],
    );
  };

  const clearAll = () => {
    onView('all');
    onCategory('all');
    onSources([]);
    onDay(undefined);
    onTagIdsChange([]);
  };

  const chip = (id: ChipId, label: string, body: React.ReactNode, onClear?: () => void) => (
    <FilterChip
      chipId={`topic-${id}`}
      label={label}
      activeSummary={summaries[id]}
      isActive={isActive[id]}
      isOpen={open === id}
      onToggle={() => setOpen((p) => (p === id ? null : id))}
      onClose={() => setOpen((p) => (p === id ? null : p))}
      onClear={onClear}
      icon={ICONS[id]}
    >
      {body}
    </FilterChip>
  );

  const closeAfter = (fn: () => void) => () => {
    fn();
    setOpen(null);
  };

  const dateOptions = useMemo(() => dates.slice(0, 30), [dates]);

  return (
    <div className="flex items-center gap-1.5 flex-wrap" data-testid="topic-filter-bar">
      {chip(
        'view',
        t('topic.filterView', 'View'),
        <div className="p-1 min-w-[12rem]">
          {VIEWS.map((v) => (
            <button key={v} className={rowCls(view === v)} onClick={closeAfter(() => onView(v))}>
              {viewLabel(v)}
            </button>
          ))}
        </div>,
        view !== 'all' ? () => onView('all') : undefined,
      )}

      {chip(
        'category',
        t('topic.filterCategory', 'Category'),
        <div className="p-1 min-w-[12rem]">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              className={rowCls(category === c)}
              onClick={closeAfter(() => onCategory(c))}
            >
              {t(`topic.cat_${c}`, c)}
            </button>
          ))}
        </div>,
        category !== 'all' ? () => onCategory('all') : undefined,
      )}

      {chip(
        'source',
        t('topic.filterSource', 'Source'),
        <div className="p-1 min-w-[14rem] max-h-[60vh] overflow-auto">
          {sources.length === 0 ? (
            <div className="px-3 py-2 text-sm text-content-3">
              {t('topic.noSources', 'No sources yet.')}
            </div>
          ) : (
            sources.map((s) => {
              const checked = selectedSources.includes(s.id);
              return (
                <button key={s.id} className={rowCls(checked)} onClick={() => toggleSource(s.id)}>
                  <span
                    className="inline-block w-2 h-2 rounded-full shrink-0"
                    style={{ backgroundColor: s.enabled ? HEALTH_DOT[s.health] : '#9ca3af' }}
                  />
                  <span className="truncate flex-1">{s.name}</span>
                  {checked && <span className="text-accent text-xs">✓</span>}
                </button>
              );
            })
          )}
        </div>,
        selectedSources.length > 0 ? () => onSources([]) : undefined,
      )}

      {chip(
        'date',
        t('topic.filterDate', 'Date'),
        <div className="p-1 min-w-[12rem] max-h-[60vh] overflow-auto">
          <button className={rowCls(!day)} onClick={closeAfter(() => onDay(undefined))}>
            {t('topic.allDates', 'All dates')}
          </button>
          {dateOptions.map((d) => (
            <button key={d} className={rowCls(day === d)} onClick={closeAfter(() => onDay(d))}>
              {d}
            </button>
          ))}
        </div>,
        day ? () => onDay(undefined) : undefined,
      )}

      {chip(
        'tag',
        t('topic.filterTag', 'Tag'),
        <div className="p-1 min-w-[14rem] max-h-64 overflow-y-auto">
          {tagOptions.length === 0 ? (
            <div className="px-3 py-2 text-sm text-content-3">
              {t('topic.noTags', 'No tags yet.')}
            </div>
          ) : (
            tagOptions.map((tg) => {
              const id = String(tg.id);
              const checked = selectedTagIds.includes(id);
              return (
                <button key={id} className={rowCls(checked)} onClick={() => toggleTag(id)}>
                  <span
                    className="inline-block w-2 h-2 rounded-full shrink-0"
                    style={{ backgroundColor: tg.color || '#6366f1' }}
                  />
                  <span className="truncate flex-1">{tg.name_zh || tg.name}</span>
                  {checked && <span className="text-accent text-xs">✓</span>}
                </button>
              );
            })
          )}
        </div>,
        selectedTagIds.length > 0 ? () => onTagIdsChange([]) : undefined,
      )}

      {searching && (
        <span className="text-[11px] text-content-4 px-1">
          {t('topic.dateDisabledWhenSearching', 'Searching all dates')}
        </span>
      )}

      {hasAny && (
        <button
          type="button"
          onClick={clearAll}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-content-2 hover:text-content hover:bg-island-2"
          title={t('topic.clearFilters', 'Clear all filters')}
        >
          <X size={12} />
          <span className="hidden lg:inline">{t('topic.clearFilters', 'Clear all filters')}</span>
        </button>
      )}
    </div>
  );
};
