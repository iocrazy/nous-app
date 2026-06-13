// frontend/components/DownloadsView/MobileFilterSheet.tsx
//
// Mobile-only bottom sheet that surfaces the full Eagle-style filter set (the
// desktop FilterBar is `hidden md:block`, leaving phones with no filter access).
// It is a thin UI shell over the SAME `useFilterBarConfig` instance owned by
// DownloadsView — passed in via props, NOT re-instantiated — so every edit flows
// straight into the existing `libraryFilterParams` effect + the server-side
// filtering already in place. Changes apply live (no Apply button).
//
// Sections are collapsible (accordion) so the sheet stays compact with 9
// dimensions; the unbounded Tags dimension uses a searchable picker
// (TagFilterSection) rather than a flat pill wall.

import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Filter, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { UseFilterBarConfigReturn } from '../../hooks/useFilterBarConfig';
import type { Tag } from '../../types';
import type { ResourceFilterType } from '../resources/resourceFilters';
import type {
  AspectBucketId,
  DatePresetId,
  DurationPresetId,
  SocialMetric,
} from '../resources/filter/types';
import { SOCIAL_METRICS } from '../resources/filter/types';
import { Pill, CollapsibleSection } from './filterSheetUi';
import { TagFilterSection } from './TagFilterSection';

interface MobileFilterSheetProps {
  open: boolean;
  onClose: () => void;
  /** The SAME config instance DownloadsView holds — do not call the hook again. */
  config: UseFilterBarConfigReturn;
  allTags: Tag[];
  availablePlatforms?: string[];
}

// ─── Static option tables (labels are English per UI-language rule) ─────────

const TYPE_OPTIONS: ReadonlyArray<{ id: ResourceFilterType; label: string }> = [
  { id: 'video', label: 'Video' },
  { id: 'image', label: 'Image' },
  { id: 'audio', label: 'Audio' },
  { id: 'document', label: 'Document' },
  { id: 'other', label: 'Other' },
];

const KNOWN_PLATFORMS: readonly string[] = [
  'douyin',
  'xiaohongshu',
  'bilibili',
  'youtube',
  'tiktok',
];
const PLATFORM_LABELS: Record<string, string> = {
  douyin: 'Douyin',
  xiaohongshu: 'Xiaohongshu',
  bilibili: 'Bilibili',
  youtube: 'YouTube',
  tiktok: 'TikTok',
  twitter: 'Twitter',
  upload: 'Upload',
  other: 'Other',
};
function platformLabel(p: string): string {
  return PLATFORM_LABELS[p] ?? p.charAt(0).toUpperCase() + p.slice(1);
}

const AI_FLAGS: ReadonlyArray<{
  key: 'transcribed' | 'summarized' | 'analyzed';
  label: string;
}> = [
  { key: 'transcribed', label: 'Transcribed' },
  { key: 'summarized', label: 'Summarized' },
  { key: 'analyzed', label: 'Analyzed' },
];

const DATE_PRESETS: ReadonlyArray<{ id: DatePresetId; label: string }> = [
  { id: 'today', label: 'Today' },
  { id: 'thisWeek', label: 'This week' },
  { id: 'thisMonth', label: 'This month' },
  { id: 'last30days', label: 'Last 30 days' },
  { id: 'last90days', label: 'Last 90 days' },
];

const DURATION_PRESETS: ReadonlyArray<{ id: DurationPresetId; label: string }> = [
  { id: 'short60s', label: '≤ 60s' },
  { id: 'medium', label: '1–5 min' },
  { id: 'long', label: '5–30 min' },
  { id: 'xlong', label: '30 min+' },
];

const ASPECT_OPTIONS: ReadonlyArray<{ id: AspectBucketId; label: string }> = [
  { id: 'portrait', label: '9:16' },
  { id: 'landscape', label: '16:9' },
  { id: 'square', label: '1:1' },
  { id: 'fourThree', label: '4:3' },
  { id: 'other', label: 'Other' },
];

const SOCIAL_LABELS: Record<SocialMetric, string> = {
  likes: 'Likes',
  comments: 'Comments',
  favorites: 'Favorites',
  shares: 'Shares',
};

function toggleIn<T>(arr: readonly T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}

// ─── The sheet ──────────────────────────────────────────────────────────────

export function MobileFilterSheet({
  open,
  onClose,
  config,
  allTags,
  availablePlatforms = [],
}: MobileFilterSheetProps) {
  const { t } = useTranslation();
  // Hooks must run unconditionally — keep state above the early return.
  const [openSections, setOpenSections] = useState<Set<string>>(new Set());

  const {
    chipValues,
    setChipValue,
    clearAll,
    clearChip,
    hasActiveFilters,
    activeFilterCount,
  } = config;

  const platforms = useMemo(
    () => Array.from(new Set<string>([...KNOWN_PLATFORMS, ...availablePlatforms])),
    [availablePlatforms],
  );

  if (!open) return null;

  const toggleSection = (id: string) =>
    setOpenSections((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const setSocialMetric = (
    m: SocialMetric,
    patch: Partial<{ enabled: boolean; threshold: number }>,
  ) => {
    const cur = chipValues.social;
    setChipValue('social', {
      ...cur,
      metrics: { ...cur.metrics, [m]: { ...cur.metrics[m], ...patch } },
    });
  };

  // ── Per-section collapsed-header summary (null = inactive) ──
  const socialActive =
    SOCIAL_METRICS.filter((m) => chipValues.social.metrics[m].enabled).length +
    (chipValues.social.hasComments ? 1 : 0);
  const aiActive = [
    chipValues.ai_status.transcribed,
    chipValues.ai_status.summarized,
    chipValues.ai_status.analyzed,
  ].filter(Boolean).length;
  const summaries: Record<string, string | null> = {
    type: chipValues.type.types.length ? `${chipValues.type.types.length}` : null,
    source: chipValues.source.platforms.length
      ? `${chipValues.source.platforms.length}`
      : null,
    ai_status: aiActive ? `${aiActive}` : null,
    date_added: chipValues.date_added.preset
      ? DATE_PRESETS.find((d) => d.id === chipValues.date_added.preset)?.label ?? '1'
      : null,
    social: socialActive ? `${socialActive}` : null,
    tags: chipValues.tags.tag_ids.length ? `${chipValues.tags.tag_ids.length}` : null,
    rating: chipValues.rating.min_rating
      ? `≥${chipValues.rating.min_rating}★`
      : null,
    aspect: chipValues.aspect.buckets.length
      ? `${chipValues.aspect.buckets.length}`
      : null,
    duration: chipValues.duration.preset
      ? DURATION_PRESETS.find((d) => d.id === chipValues.duration.preset)?.label ?? '1'
      : null,
  };

  const section = (
    id: string,
    title: string,
    children: React.ReactNode,
  ) => (
    <CollapsibleSection
      title={title}
      summary={summaries[id]}
      open={openSections.has(id)}
      onToggle={() => toggleSection(id)}
    >
      {children}
    </CollapsibleSection>
  );

  return createPortal(
    <div
      className="md:hidden fixed inset-0 z-[60]"
      role="dialog"
      aria-modal="true"
      aria-label={t('resources.filter.title', 'Filters')}
    >
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200"
        onClick={onClose}
      />

      <div
        className="absolute inset-x-0 bottom-0 bg-ink-900 border-t border-ink-700/80 rounded-t-2xl shadow-2xl max-h-[82vh] flex flex-col animate-in slide-in-from-bottom duration-200"
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      >
        {/* Grab handle */}
        <div className="pt-2 flex justify-center shrink-0">
          <div className="h-1 w-10 rounded-full bg-ink-600" />
        </div>

        {/* Header */}
        <div className="px-4 py-3 flex items-center justify-between border-b border-ink-800 shrink-0">
          <div className="flex items-center gap-2">
            <Filter size={16} className="text-indigo-400" />
            <span className="text-sm font-semibold text-white">
              {t('resources.filter.title', 'Filters')}
            </span>
            {activeFilterCount > 0 && (
              <span className="min-w-[18px] h-[18px] px-1 rounded-full bg-indigo-500 text-white text-[10px] font-bold inline-flex items-center justify-center">
                {activeFilterCount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={clearAll}
              disabled={!hasActiveFilters}
              className="text-xs font-medium text-ink-400 disabled:opacity-40 active:text-white"
            >
              {t('resources.filter.reset', 'Reset')}
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label={t('common.close', 'Close')}
              className="text-ink-400 active:text-white"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Scrollable body — collapsible sections */}
        <div className="flex-1 overflow-y-auto px-4">
          {/* Type */}
          {section(
            'type',
            t('resources.filter.type', 'Type'),
            <div className="flex flex-wrap gap-2">
              {TYPE_OPTIONS.map((o) => (
                <Pill
                  key={o.id}
                  active={chipValues.type.types.includes(o.id)}
                  onClick={() =>
                    setChipValue('type', {
                      types: toggleIn(chipValues.type.types, o.id),
                    })
                  }
                >
                  {o.label}
                </Pill>
              ))}
            </div>,
          )}

          {/* Source / platform */}
          {section(
            'source',
            t('resources.filter.source', 'Source'),
            <div className="flex flex-wrap gap-2">
              {platforms.map((p) => (
                <Pill
                  key={p}
                  active={chipValues.source.platforms.includes(p)}
                  onClick={() =>
                    setChipValue('source', {
                      platforms: toggleIn(chipValues.source.platforms, p),
                    })
                  }
                >
                  {platformLabel(p)}
                </Pill>
              ))}
            </div>,
          )}

          {/* Tags — searchable picker */}
          {section(
            'tags',
            t('resources.filter.tags', 'Tags'),
            <TagFilterSection
              allTags={allTags}
              selectedIds={chipValues.tags.tag_ids}
              onToggle={(id) =>
                setChipValue('tags', {
                  tag_ids: toggleIn(chipValues.tags.tag_ids, id),
                })
              }
              onClear={() => clearChip('tags')}
            />,
          )}

          {/* AI status */}
          {section(
            'ai_status',
            t('resources.filter.aiStatus', 'AI status'),
            <div className="flex flex-wrap gap-2">
              {AI_FLAGS.map((f) => (
                <Pill
                  key={f.key}
                  active={chipValues.ai_status[f.key]}
                  onClick={() =>
                    setChipValue('ai_status', {
                      ...chipValues.ai_status,
                      [f.key]: !chipValues.ai_status[f.key],
                    })
                  }
                >
                  {f.label}
                </Pill>
              ))}
            </div>,
          )}

          {/* Date added */}
          {section(
            'date_added',
            t('resources.filter.dateAdded', 'Date added'),
            <div className="flex flex-wrap gap-2">
              {DATE_PRESETS.map((d) => {
                const active = chipValues.date_added.preset === d.id;
                return (
                  <Pill
                    key={d.id}
                    active={active}
                    onClick={() =>
                      setChipValue('date_added', {
                        preset: active ? null : d.id,
                        customAfter: null,
                        customBefore: null,
                      })
                    }
                  >
                    {d.label}
                  </Pill>
                );
              })}
            </div>,
          )}

          {/* Social */}
          {section(
            'social',
            t('resources.filter.socialLabel', 'Social'),
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Pill
                  active={chipValues.social.combine === 'and'}
                  onClick={() =>
                    setChipValue('social', { ...chipValues.social, combine: 'and' })
                  }
                >
                  AND
                </Pill>
                <Pill
                  active={chipValues.social.combine === 'or'}
                  onClick={() =>
                    setChipValue('social', { ...chipValues.social, combine: 'or' })
                  }
                >
                  OR
                </Pill>
                <div className="ml-auto">
                  <Pill
                    active={chipValues.social.hasComments}
                    onClick={() =>
                      setChipValue('social', {
                        ...chipValues.social,
                        hasComments: !chipValues.social.hasComments,
                      })
                    }
                  >
                    {t('resources.filter.social.hasCommentsShort', 'Has comments')}
                  </Pill>
                </div>
              </div>
              {SOCIAL_METRICS.map((m) => {
                const entry = chipValues.social.metrics[m];
                return (
                  <div key={m} className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setSocialMetric(m, { enabled: !entry.enabled })}
                      className={`w-24 px-3 py-1.5 rounded-lg text-xs font-medium border text-left transition-colors ${
                        entry.enabled
                          ? 'bg-indigo-500 border-indigo-400 text-white'
                          : 'bg-ink-800 border-ink-700 text-ink-300'
                      }`}
                    >
                      {SOCIAL_LABELS[m]}
                    </button>
                    <span className="text-xs text-ink-500">≥</span>
                    <input
                      type="number"
                      inputMode="numeric"
                      min={0}
                      disabled={!entry.enabled}
                      value={entry.threshold}
                      onChange={(e) => {
                        const n = Math.max(0, Math.floor(Number(e.target.value) || 0));
                        setSocialMetric(m, { threshold: n });
                      }}
                      className="flex-1 min-w-0 bg-ink-800 border border-ink-700 rounded-lg px-3 py-1.5 text-sm text-white outline-none focus:border-indigo-400 disabled:opacity-40"
                    />
                  </div>
                );
              })}
            </div>,
          )}

          {/* Rating */}
          {section(
            'rating',
            t('resources.filter.rating', 'Rating'),
            <div className="flex flex-wrap gap-2">
              {[1, 2, 3, 4, 5].map((n) => {
                const active = chipValues.rating.min_rating === n;
                return (
                  <Pill
                    key={n}
                    active={active}
                    onClick={() =>
                      setChipValue('rating', { min_rating: active ? 0 : n })
                    }
                  >
                    {`★ ${n}+`}
                  </Pill>
                );
              })}
            </div>,
          )}

          {/* Aspect */}
          {section(
            'aspect',
            t('resources.filter.aspect', 'Aspect ratio'),
            <div className="flex flex-wrap gap-2">
              {ASPECT_OPTIONS.map((o) => (
                <Pill
                  key={o.id}
                  active={chipValues.aspect.buckets.includes(o.id)}
                  onClick={() =>
                    setChipValue('aspect', {
                      buckets: toggleIn(chipValues.aspect.buckets, o.id),
                    })
                  }
                >
                  {o.label}
                </Pill>
              ))}
            </div>,
          )}

          {/* Duration */}
          {section(
            'duration',
            t('resources.filter.duration', 'Duration'),
            <div className="flex flex-wrap gap-2">
              {DURATION_PRESETS.map((d) => {
                const active = chipValues.duration.preset === d.id;
                return (
                  <Pill
                    key={d.id}
                    active={active}
                    onClick={() =>
                      setChipValue('duration', {
                        preset: active ? null : d.id,
                        customMin: null,
                        customMax: null,
                      })
                    }
                  >
                    {d.label}
                  </Pill>
                );
              })}
            </div>,
          )}
        </div>

        {/* Done */}
        <div className="px-4 py-3 border-t border-ink-800 shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="w-full py-2.5 rounded-xl bg-indigo-500 text-white text-sm font-semibold active:bg-indigo-600"
          >
            {t('common.done', 'Done')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
