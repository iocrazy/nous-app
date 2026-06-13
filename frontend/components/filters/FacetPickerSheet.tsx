// frontend/components/filters/FacetPickerSheet.tsx
//
// Full-screen picker for ONE filter dimension (Pixcall-style: ✕ / title / ✓,
// own search for the unbounded Tags facet). Edits apply live to the shared
// useFilterBarConfig instance passed in — the ✓ just closes. Each dimension
// keeps its own focused screen so the unbounded Tags catalog never competes
// for space with the bounded facets.

import { useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Check, Search, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { UseFilterBarConfigReturn } from '../../hooks/useFilterBarConfig';
import type { ChipId, SocialMetric } from '../resources/filter/types';
import { SOCIAL_METRICS } from '../resources/filter/types';
import type { Tag } from '../../types';
import { Pill } from '../DownloadsView/filterSheetUi';
import {
  FACETS,
  TYPE_OPTIONS,
  KNOWN_PLATFORMS,
  platformLabel,
  AI_FLAGS,
  DATE_PRESETS,
  DURATION_PRESETS,
  ASPECT_OPTIONS,
  SOCIAL_LABELS,
} from './facetMeta';

interface FacetPickerSheetProps {
  open: boolean;
  facetId: ChipId | null;
  onClose: () => void;
  config: UseFilterBarConfigReturn;
  allTags: Tag[];
  availablePlatforms?: string[];
  /** Create a new tag from the search box. When omitted, the create row is
   *  hidden (e.g. scopes where the parent can't refresh its tag list). */
  onCreateTag?: (name: string) => Promise<Tag>;
}

function toggleIn<T>(arr: readonly T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}
function tagCount(t: Tag): number {
  return t.media_count ?? t.video_count ?? 0;
}

export function FacetPickerSheet({
  open,
  facetId,
  onClose,
  config,
  allTags,
  availablePlatforms = [],
  onCreateTag,
}: FacetPickerSheetProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  // Swipe-down-to-dismiss (drag from the header/handle area).
  const [dragY, setDragY] = useState(0);
  const [dragging, setDragging] = useState(false);
  const dragStartY = useRef<number | null>(null);

  const { chipValues, setChipValue, clearChip, isChipActive } = config;

  // Tag list: selected first, then by usage; filtered by the search query.
  const tagRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const selected = new Set(chipValues.tags.tag_ids);
    return allTags
      .filter(
        (tag) =>
          !q ||
          tag.name.toLowerCase().includes(q) ||
          (tag.name_zh ?? '').toLowerCase().includes(q),
      )
      .sort((a, b) => {
        const sa = selected.has(a.id) ? 1 : 0;
        const sb = selected.has(b.id) ? 1 : 0;
        if (sa !== sb) return sb - sa;
        const d = tagCount(b) - tagCount(a);
        return d !== 0 ? d : a.name.localeCompare(b.name);
      });
  }, [allTags, query, chipValues.tags.tag_ids]);

  // Group the filtered tags by category; ungrouped ('') sorts last.
  const groupedTags = useMemo(() => {
    const map = new Map<string, Tag[]>();
    for (const tag of tagRows) {
      const g = tag.group_name || '';
      const arr = map.get(g);
      if (arr) arr.push(tag);
      else map.set(g, [tag]);
    }
    return Array.from(map.entries()).sort((a, b) => {
      if (a[0] === '') return 1;
      if (b[0] === '') return -1;
      return a[0].localeCompare(b[0]);
    });
  }, [tagRows]);

  if (!open || !facetId) return null;

  const facet = FACETS.find((f) => f.id === facetId);
  const platforms = Array.from(
    new Set<string>([...KNOWN_PLATFORMS, ...availablePlatforms]),
  );
  const selectedTagSet = new Set(chipValues.tags.tag_ids);

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

  const isTags = facetId === 'tags';

  const trimmed = query.trim();
  const exactExists = trimmed
    ? allTags.some((tg) => tg.name.toLowerCase() === trimmed.toLowerCase())
    : true;
  const showCreate = !!onCreateTag && trimmed.length > 0 && !exactExists;
  const handleCreate = async () => {
    if (!onCreateTag || creating) return;
    const name = query.trim();
    if (!name) return;
    setCreating(true);
    try {
      const tag = await onCreateTag(name);
      setChipValue('tags', {
        tag_ids: [...chipValues.tags.tag_ids, tag.id],
      });
      setQuery('');
    } catch (err) {
      console.error('[FacetPickerSheet] create tag failed:', err);
    } finally {
      setCreating(false);
    }
  };

  const renderTagRow = (tag: Tag) => {
    const checked = selectedTagSet.has(tag.id);
    return (
      <button
        key={tag.id}
        type="button"
        onClick={() =>
          setChipValue('tags', {
            tag_ids: toggleIn(chipValues.tags.tag_ids, tag.id),
          })
        }
        className={`w-full px-4 py-3 flex items-center gap-3 text-left border-b border-ink-800/50 ${
          checked ? 'bg-indigo-500/10' : 'active:bg-ink-800/50'
        }`}
      >
        <span
          className={`w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${
            checked ? 'bg-indigo-500 text-white' : 'border border-ink-600'
          }`}
        >
          {checked && <Check size={13} />}
        </span>
        <span className="text-sm text-white flex-1 truncate">{tag.name}</span>
        <span className="text-[11px] text-ink-500 shrink-0">{tagCount(tag)}</span>
      </button>
    );
  };

  const onDragStart = (e: React.TouchEvent) => {
    dragStartY.current = e.touches[0].clientY;
    setDragging(true);
  };
  const onDragMove = (e: React.TouchEvent) => {
    if (dragStartY.current == null) return;
    setDragY(Math.max(0, e.touches[0].clientY - dragStartY.current));
  };
  const onDragEnd = () => {
    setDragging(false);
    if (dragY > 90) onClose();
    setDragY(0);
    dragStartY.current = null;
  };

  return createPortal(
    <div
      className="md:hidden fixed inset-0 z-[70] bg-ink-950 flex flex-col animate-in slide-in-from-bottom duration-300"
      style={{
        transform: dragY ? `translateY(${dragY}px)` : undefined,
        transition: dragging ? 'none' : 'transform 0.2s ease',
      }}
    >
      {/* Grab handle + header — drag down here to dismiss */}
      <div
        onTouchStart={onDragStart}
        onTouchMove={onDragMove}
        onTouchEnd={onDragEnd}
        className="shrink-0"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 8px)' }}
      >
        <div className="flex justify-center pb-1.5">
          <div className="h-1 w-10 rounded-full bg-ink-600" />
        </div>
        <div className="px-4 py-2.5 flex items-center justify-between border-b border-ink-800">
        <button
          type="button"
          onClick={onClose}
          aria-label={t('common.close', 'Close')}
          className="w-9 h-9 rounded-full bg-ink-800 flex items-center justify-center text-ink-300 active:bg-ink-700"
        >
          <X size={18} />
        </button>
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-white">
            {t('resources.filter.select', 'Select')} {facet?.label ?? ''}
          </span>
          {isChipActive(facetId) && (
            <button
              type="button"
              onClick={() => clearChip(facetId)}
              className="text-[11px] text-indigo-400 active:text-indigo-300"
            >
              {t('resources.filter.clear', 'Clear')}
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('common.done', 'Done')}
          className="w-9 h-9 rounded-full bg-indigo-500 flex items-center justify-center text-white active:bg-indigo-600"
        >
          <Check size={18} />
        </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {isTags ? (
          <div>
            {/* Create-new row (when the typed name doesn't already exist) */}
            {showCreate && (
              <button
                type="button"
                onClick={handleCreate}
                disabled={creating}
                className="w-full px-4 py-3 flex items-center gap-3 text-left border-b border-ink-800/50 active:bg-ink-800/50 disabled:opacity-50"
              >
                <span className="w-5 h-5 rounded-md flex items-center justify-center shrink-0 bg-indigo-500 text-white">
                  <Plus size={13} />
                </span>
                <span className="text-sm text-indigo-300 flex-1 truncate">
                  {t('resources.filter.createTag', 'Create "{{name}}"', {
                    name: trimmed,
                  })}
                </span>
              </button>
            )}

            {tagRows.length === 0 && !showCreate ? (
              <div className="px-4 py-6 text-sm text-ink-500">
                {t('resources.filter.noTags', 'No matching tags')}
              </div>
            ) : (
              groupedTags.map(([group, tags]) => (
                <div key={group || '__ungrouped__'}>
                  <div className="px-4 pt-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-500">
                    {group || t('resources.filter.ungrouped', 'Ungrouped')}
                  </div>
                  {tags.map(renderTagRow)}
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="px-4 py-4 space-y-4">
            {facetId === 'type' && (
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
              </div>
            )}

            {facetId === 'source' && (
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
              </div>
            )}

            {facetId === 'ai_status' && (
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
              </div>
            )}

            {facetId === 'date_added' && (
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
              </div>
            )}

            {facetId === 'duration' && (
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
              </div>
            )}

            {facetId === 'aspect' && (
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
              </div>
            )}

            {facetId === 'rating' && (
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
              </div>
            )}

            {facetId === 'social' && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Pill
                    active={chipValues.social.combine === 'and'}
                    onClick={() =>
                      setChipValue('social', {
                        ...chipValues.social,
                        combine: 'and',
                      })
                    }
                  >
                    AND
                  </Pill>
                  <Pill
                    active={chipValues.social.combine === 'or'}
                    onClick={() =>
                      setChipValue('social', {
                        ...chipValues.social,
                        combine: 'or',
                      })
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
                        onClick={() =>
                          setSocialMetric(m, { enabled: !entry.enabled })
                        }
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
                          const n = Math.max(
                            0,
                            Math.floor(Number(e.target.value) || 0),
                          );
                          setSocialMetric(m, { threshold: n });
                        }}
                        className="flex-1 min-w-0 bg-ink-800 border border-ink-700 rounded-lg px-3 py-1.5 text-sm text-white outline-none focus:border-indigo-400 disabled:opacity-40"
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Bottom search — Tags only */}
      {isTags && (
        <div
          className="px-4 py-3 border-t border-ink-800 shrink-0"
          style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)' }}
        >
          <div className="flex items-center gap-2 bg-ink-800 border border-ink-700 rounded-full px-4 py-2.5">
            <Search size={16} className="text-ink-500 shrink-0" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('resources.filter.searchTags', 'Search tags…')}
              className="flex-1 min-w-0 bg-transparent outline-none text-sm text-white placeholder-ink-500"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label={t('common.clear', 'Clear')}
                className="text-ink-500 active:text-white"
              >
                <X size={14} />
              </button>
            )}
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
}
