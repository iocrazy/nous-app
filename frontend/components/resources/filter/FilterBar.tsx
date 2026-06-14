// frontend/components/resources/filter/FilterBar.tsx
//
// Eagle-style pinnable filter bar for the Resources toolbar. Renders
// pinned chips in order, plus the Filter-config button and a global
// "Clear all" when any chip is active.
//
// Keeps its own open-chip state: only one chip dropdown / the config
// popover is open at a time. Dropdown positioning is handled inside
// FilterChip itself.

import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Calendar,
  Clock,
  Globe,
  Heart,
  Layers,
  ListFilterPlus,
  RectangleHorizontal,
  Sparkles,
  Star,
  Tag as TagIcon,
  X,
  type LucideIcon,
} from 'lucide-react';

import type { Tag } from '../../../types';
import type { UseFilterBarConfigReturn } from '../../../hooks/useFilterBarConfig';
import type { ResourceFilterType } from '../resourceFilters';
import type { ChipId, DatePresetId, DurationPresetId, SocialMetric } from './types';
import { SOCIAL_METRICS } from './types';
import { filterVisibleChips } from './chipVisibility';
import { FilterChip } from './FilterChip';
import { SegmentedTypeFilter } from './SegmentedTypeFilter';
import { FilterConfigPanel } from './FilterConfigPanel';
import { islandUI } from '../../../utils/featureFlags';
import { RatingFilterDropdown } from './RatingFilterDropdown';
import { TagsFilterDropdown } from './TagsFilterDropdown';
import { TypeFilterDropdown } from './TypeFilterDropdown';
import { SourceFilterDropdown } from './SourceFilterDropdown';
import { AIStatusFilterDropdown } from './AIStatusFilterDropdown';
import { DateAddedFilterDropdown } from './DateAddedFilterDropdown';
import { DurationFilterDropdown } from './DurationFilterDropdown';
import { AspectFilterDropdown } from './AspectFilterDropdown';
import { SocialFilterDropdown } from './SocialFilterDropdown';
import { mergeTags as mergeTagsApi } from '../../../services/unifiedTagService';
import { useResourcesContext } from '../../../contexts/ResourcesContext';
import { datePresetSummary } from './dateUtils';
import { durationPresetSummary } from './durationUtils';
import { aspectSummary } from './aspectUtils';

export interface FilterBarProps {
  /** Config hook instance — FilterBar is controlled via this. */
  config: UseFilterBarConfigReturn;
  /** All tags available for selection in the Tags chip. */
  allTags: Tag[];
  /** Platforms observed in the currently-loaded resource set. Used to
   *  enrich the Source chip's option list beyond the hardcoded known
   *  platforms. Optional — defaults to empty. */
  availablePlatforms?: string[];
  /** Optional per-scope allowlist. When provided, chips outside this
   *  set are hidden from the toolbar and from the filter-settings
   *  popover's AVAILABLE section. A user's persisted pin state for a
   *  hidden chip is preserved in localStorage — the chip just isn't
   *  rendered in scopes that disallow it (e.g. Source + Social are
   *  hidden in "My Uploads" because uploaded files have no download
   *  platform or like counts). Undefined = no filtering (all chips
   *  visible). */
  allowedChips?: ReadonlyArray<ChipId>;
}

type OpenTarget = { kind: 'chip'; id: ChipId } | { kind: 'config' } | null;

/** Compact number formatter for the Social chip summary.
 *  1234 -> "1.2K", 150000 -> "150K", 2500000 -> "2.5M". */
function formatCompact(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '0';
  if (n >= 1_000_000) {
    const v = n / 1_000_000;
    return `${v >= 10 ? v.toFixed(0) : v.toFixed(1).replace(/\.0$/, '')}M`;
  }
  if (n >= 1_000) {
    const v = n / 1_000;
    return `${v >= 10 ? v.toFixed(0) : v.toFixed(1).replace(/\.0$/, '')}K`;
  }
  return String(n);
}

/** Build the summary shown after the chip label when Social is active. */
function socialSummary(
  value: import('./types').SocialChipValue,
  t: (key: string, fallback?: string) => string,
): string | null {
  const enabled = SOCIAL_METRICS.filter((m) => value.metrics[m].enabled);
  if (enabled.length === 0 && !value.hasComments) return null;

  const shortLabels: Record<SocialMetric, string> = {
    likes: t('resources.filter.social.likesShort', 'likes'),
    comments: t('resources.filter.social.commentsShort', 'comments'),
    favorites: t('resources.filter.social.favoritesShort', 'favs'),
    shares: t('resources.filter.social.sharesShort', 'shares'),
  };

  const parts: string[] = enabled.map(
    (m) => `${shortLabels[m]}≥${formatCompact(value.metrics[m].threshold)}`,
  );
  if (parts.length === 1 && !value.hasComments) return parts[0];

  // Join enabled thresholds with the combine symbol. Keep it short so
  // the chip doesn't wrap.
  const joiner = value.combine === 'or' ? ' | ' : ' & ';
  let out = parts.join(joiner);

  if (value.hasComments) {
    const hc = t('resources.filter.social.hasCommentsShort', 'has comments');
    out = out ? `${out} & ${hc}` : hc;
  }

  // Cap at a reasonable length so very long thresholds don't distort layout.
  if (out.length > 32) return `${enabled.length + (value.hasComments ? 1 : 0)}`;
  return out;
}

export const FilterBar: React.FC<FilterBarProps> = ({
  config,
  allTags,
  availablePlatforms = [],
  allowedChips,
}) => {
  const { t } = useTranslation();
  const [openTarget, setOpenTarget] = useState<OpenTarget>(null);
  const { refreshTags } = useResourcesContext();

  const {
    pinnedChips,
    availableChips,
    chipValues,
    hasActiveFilters,
    pinChip,
    unpinChip,
    reorderChips,
    setChipValue,
    clearChip,
    clearAll,
    isChipActive,
  } = config;

  const handleMergeTags = React.useCallback(
    async (targetId: string, sourceIds: string[]) => {
      await mergeTagsApi(targetId, sourceIds);
      setChipValue('tags', { tag_ids: [targetId] });
      await refreshTags();
    },
    [setChipValue, refreshTags],
  );

  // Island shell surfaces the media-type filter as an always-visible segmented
  // control (spec §5.1) instead of the `type` dropdown chip, so drop `type` from
  // the pinned chips here to avoid a duplicate control. Same multi-select state
  // drives both, so nothing is lost (D12). Build-time constant → stable.
  const island = islandUI();

  // Subset of pinnedChips that's actually rendered in the toolbar.
  // Disallowed chips stay pinned in localStorage but don't show here.
  const visiblePinnedChips = useMemo(
    () =>
      filterVisibleChips(pinnedChips, allowedChips).filter(
        (id) => !(island && id === 'type'),
      ),
    [pinnedChips, allowedChips, island],
  );

  // Static icon registry — each chip carries a distinct lucide icon so the
  // bar reads at a glance even when no chip is active.
  const CHIP_ICONS: Record<ChipId, LucideIcon> = {
    tags: TagIcon,
    rating: Star,
    type: Layers,
    source: Globe,
    ai_status: Sparkles,
    date_added: Calendar,
    duration: Clock,
    aspect: RectangleHorizontal,
    social: Heart,
  };

  const chipLabel = useMemo(() => {
    return (id: ChipId): string => {
      switch (id) {
        case 'tags':
          return t('resources.filter.tags', 'Tags');
        case 'rating':
          return t('resources.filter.rating', 'Rating');
        case 'type':
          return t('resources.filter.type', 'Type');
        case 'source':
          return t('resources.filter.source', 'Source');
        case 'ai_status':
          return t('resources.filter.aiStatus', 'AI');
        case 'date_added':
          return t('resources.filter.dateAdded', 'Date');
        case 'duration':
          return t('resources.filter.duration', 'Duration');
        case 'aspect':
          return t('resources.filter.aspect', 'Aspect');
        case 'social':
          return t('resources.filter.socialLabel', 'Social');
      }
    };
  }, [t]);

  const chipSummary = (id: ChipId): string | null => {
    switch (id) {
      case 'tags': {
        const n = chipValues.tags.tag_ids.length;
        return n > 0 ? String(n) : null;
      }
      case 'rating': {
        const r = chipValues.rating.min_rating;
        return r > 0 ? `≥${r}★` : null;
      }
      case 'type': {
        const types = chipValues.type.types;
        if (types.length === 0) return null;
        if (types.length === 1) {
          const key = `smartFolder.fileTypes.${types[0]}`;
          return t(key, types[0]);
        }
        return String(types.length);
      }
      case 'source': {
        const platforms = chipValues.source.platforms;
        if (platforms.length === 0) return null;
        if (platforms.length === 1) {
          const p = platforms[0];
          return p.charAt(0).toUpperCase() + p.slice(1);
        }
        return String(platforms.length);
      }
      case 'ai_status': {
        const ai = chipValues.ai_status;
        const parts: string[] = [];
        if (ai.transcribed) parts.push(t('resources.filter.ai.transcribed', 'Transcribed'));
        if (ai.summarized) parts.push(t('resources.filter.ai.summarized', 'Summarized'));
        if (ai.analyzed) parts.push(t('resources.filter.ai.analyzed', 'Analyzed'));
        if (parts.length === 0) return null;
        if (parts.length <= 2) return parts.join(', ');
        return String(parts.length);
      }
      case 'date_added': {
        const presetLabels: Record<DatePresetId, string> = {
          today: t('resources.filter.date.today', 'Today'),
          thisWeek: t('resources.filter.date.thisWeek', 'This week'),
          thisMonth: t('resources.filter.date.thisMonth', 'This month'),
          last30days: t('resources.filter.date.last30days', 'Last 30 days'),
          last90days: t('resources.filter.date.last90days', 'Last 90 days'),
          custom: t('resources.filter.date.custom', 'Custom range'),
        };
        return datePresetSummary(chipValues.date_added, presetLabels);
      }
      case 'duration': {
        const presetLabels: Record<DurationPresetId, string> = {
          short60s: t('resources.filter.durationPresets.short60s', '≤ 60s'),
          medium: t('resources.filter.durationPresets.medium', '1-5 min'),
          long: t('resources.filter.durationPresets.long', '5-30 min'),
          xlong: t('resources.filter.durationPresets.xlong', '30+ min'),
          custom: t('resources.filter.durationPresets.custom', 'Custom'),
        };
        return durationPresetSummary(chipValues.duration, presetLabels);
      }
      case 'aspect':
        return aspectSummary(chipValues.aspect);
      case 'social':
        return socialSummary(chipValues.social, t);
    }
  };

  const renderDropdown = (id: ChipId): React.ReactNode => {
    switch (id) {
      case 'tags':
        return (
          <TagsFilterDropdown
            allTags={allTags}
            selectedTagIds={chipValues.tags.tag_ids}
            onChange={(next) => setChipValue('tags', { tag_ids: next })}
            onClearAll={() => clearChip('tags')}
            onMergeTags={handleMergeTags}
          />
        );
      case 'rating':
        return (
          <RatingFilterDropdown
            minRating={chipValues.rating.min_rating}
            onChange={(next) => {
              setChipValue('rating', { min_rating: next });
              if (next === 0) {
                // Auto-close on "Any" pick — feels snappier.
                setOpenTarget(null);
              }
            }}
          />
        );
      case 'type':
        return (
          <TypeFilterDropdown
            selectedTypes={chipValues.type.types}
            onChange={(next: ResourceFilterType[]) =>
              setChipValue('type', { types: next })
            }
            onClearAll={() => clearChip('type')}
          />
        );
      case 'source':
        return (
          <SourceFilterDropdown
            selectedPlatforms={chipValues.source.platforms}
            availablePlatforms={availablePlatforms}
            onChange={(next) => setChipValue('source', { platforms: next })}
            onClearAll={() => clearChip('source')}
          />
        );
      case 'ai_status':
        return (
          <AIStatusFilterDropdown
            value={chipValues.ai_status}
            onChange={(next) => setChipValue('ai_status', next)}
            onClearAll={() => clearChip('ai_status')}
          />
        );
      case 'date_added':
        return (
          <DateAddedFilterDropdown
            value={chipValues.date_added}
            onChange={(next) => setChipValue('date_added', next)}
            onClearAll={() => clearChip('date_added')}
          />
        );
      case 'duration':
        return (
          <DurationFilterDropdown
            value={chipValues.duration}
            onChange={(next) => setChipValue('duration', next)}
            onClearAll={() => clearChip('duration')}
          />
        );
      case 'aspect':
        return (
          <AspectFilterDropdown
            selectedBuckets={chipValues.aspect.buckets}
            onChange={(next) => setChipValue('aspect', { buckets: next })}
            onClearAll={() => clearChip('aspect')}
          />
        );
      case 'social':
        return (
          <SocialFilterDropdown
            value={chipValues.social}
            onChange={(next) => setChipValue('social', next)}
            onClearAll={() => clearChip('social')}
          />
        );
    }
  };

  const isChipOpen = (id: ChipId) =>
    openTarget?.kind === 'chip' && openTarget.id === id;
  const isConfigOpen = openTarget?.kind === 'config';

  return (
    <div className="flex items-center gap-1.5 flex-wrap" data-testid="resources-filter-bar">
      {island && (
        <SegmentedTypeFilter
          selected={chipValues.type.types}
          onChange={(next) => setChipValue('type', { types: next })}
        />
      )}
      {visiblePinnedChips.map((id) => (
        <FilterChip
          key={id}
          chipId={id}
          label={chipLabel(id)}
          activeSummary={chipSummary(id)}
          isActive={isChipActive(id)}
          isOpen={isChipOpen(id)}
          onToggle={() => setOpenTarget(isChipOpen(id) ? null : { kind: 'chip', id })}
          onClose={() => setOpenTarget((prev) => (prev?.kind === 'chip' && prev.id === id ? null : prev))}
          onClear={() => clearChip(id)}
          icon={CHIP_ICONS[id]}
        >
          {renderDropdown(id)}
        </FilterChip>
      ))}

      {/* Filter config button — a funnel-plus to signal "configure / add
          filters" (vs the plain funnel in the search row, which toggles
          the whole bar's visibility). */}
      <div className="relative">
        <button
          type="button"
          onClick={() =>
            setOpenTarget((prev) => (prev?.kind === 'config' ? null : { kind: 'config' }))
          }
          className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg border transition-colors text-xs ${
            isConfigOpen
              ? 'border-indigo-500/60 bg-indigo-500/10 text-indigo-300'
              : 'border-ink-700/80 bg-ink-900/40 text-ink-400 hover:text-ink-200 hover:border-ink-600'
          }`}
          title={t('resources.filter.filterConfig', 'Filter Settings')}
          aria-label={t('resources.filter.filterConfig', 'Filter Settings')}
          aria-haspopup="dialog"
          aria-expanded={isConfigOpen}
        >
          <ListFilterPlus size={12} />
        </button>
        {isConfigOpen && (
          <FilterConfigPanel
            pinnedChips={pinnedChips}
            availableChips={availableChips}
            allowedChips={allowedChips}
            chipLabel={chipLabel}
            onPin={pinChip}
            onUnpin={unpinChip}
            onReorder={reorderChips}
            onClose={() => setOpenTarget(null)}
          />
        )}
      </div>

      {hasActiveFilters && (
        <button
          type="button"
          onClick={clearAll}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-ink-400 hover:text-ink-100 hover:bg-ink-800/60"
          title={t('resources.filter.clearAll', 'Clear all filters')}
        >
          <X size={12} />
          <span className="hidden lg:inline">
            {t('resources.filter.clearAll', 'Clear all filters')}
          </span>
        </button>
      )}
    </div>
  );
};
