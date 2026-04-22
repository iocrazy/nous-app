// frontend/hooks/useFilterBarConfig.ts
//
// Persistent configuration + values for the Resources pinnable filter bar.
// State is kept entirely in-memory and mirrored to localStorage under
// `resourceFilterConfig`. The hook API is intentionally URL-friendly:
// every mutation returns via immutable copies so a future PR can sync
// `chipValues` to / from URL query params without reshaping the hook.

import { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  AspectBucketId,
  ChipId,
  ChipValuesMap,
  DatePresetId,
  DurationPresetId,
  FetchResourcesFilterParams,
  SocialChipValue,
  SocialMetric,
} from '../components/resources/filter/types';
import {
  CHIP_IDS,
  DEFAULT_CHIP_VALUES,
  DEFAULT_PINNED_CHIPS,
  SOCIAL_METRICS,
} from '../components/resources/filter/types';
import { datePresetToRange } from '../components/resources/filter/dateUtils';
import { durationPresetToRange } from '../components/resources/filter/durationUtils';
import { bucketToApiValue } from '../components/resources/filter/aspectUtils';

const STORAGE_KEY = 'resourceFilterConfig';

const DATE_PRESETS: ReadonlyArray<DatePresetId> = [
  'today',
  'thisWeek',
  'thisMonth',
  'last30days',
  'last90days',
  'custom',
] as const;

const DURATION_PRESETS: ReadonlyArray<DurationPresetId> = [
  'short60s',
  'medium',
  'long',
  'xlong',
  'custom',
] as const;

const ASPECT_BUCKETS: ReadonlyArray<AspectBucketId> = [
  'portrait',
  'landscape',
  'square',
  'fourThree',
  'other',
] as const;

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

interface PersistedShape {
  pinnedChips: ChipId[];
  chipValues: ChipValuesMap;
}

const DEFAULT_STATE: PersistedShape = {
  pinnedChips: [...DEFAULT_PINNED_CHIPS],
  chipValues: DEFAULT_CHIP_VALUES,
};

/**
 * Load persisted state from localStorage with defensive fallbacks.
 * Unknown chip ids are dropped silently so removing a chip in a future
 * release doesn't leave users with a broken toolbar.
 */
function loadFromStorage(): PersistedShape {
  if (typeof window === 'undefined') return cloneDefaults();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return cloneDefaults();
    const parsed = JSON.parse(raw) as Partial<PersistedShape> | null;
    if (!parsed || typeof parsed !== 'object') return cloneDefaults();

    const validIds = new Set<ChipId>(CHIP_IDS);
    const pinnedChips = Array.isArray(parsed.pinnedChips)
      ? (parsed.pinnedChips.filter((id): id is ChipId =>
          typeof id === 'string' && validIds.has(id as ChipId),
        ) as ChipId[])
      : [...DEFAULT_PINNED_CHIPS];

    const srcValues = (parsed.chipValues ?? {}) as Partial<ChipValuesMap>;

    const chipValues: ChipValuesMap = {
      tags: {
        tag_ids: Array.isArray(srcValues.tags?.tag_ids)
          ? (srcValues.tags!.tag_ids.filter(
              (id) => typeof id === 'string',
            ) as string[])
          : [],
      },
      rating: {
        min_rating: clampRating(srcValues.rating?.min_rating),
      },
      type: {
        types: Array.isArray(srcValues.type?.types)
          ? (srcValues.type!.types.filter(
              (t) =>
                t === 'video' ||
                t === 'image' ||
                t === 'audio' ||
                t === 'document' ||
                t === 'other',
            ) as ChipValuesMap['type']['types'])
          : [],
      },
      source: {
        platforms: Array.isArray(srcValues.source?.platforms)
          ? (srcValues.source!.platforms.filter(
              (p) => typeof p === 'string' && p.length > 0,
            ) as string[])
          : [],
      },
      ai_status: {
        transcribed: Boolean(srcValues.ai_status?.transcribed),
        summarized: Boolean(srcValues.ai_status?.summarized),
        analyzed: Boolean(srcValues.ai_status?.analyzed),
      },
      date_added: sanitizeDateAdded(srcValues.date_added),
      duration: sanitizeDuration(srcValues.duration),
      aspect: sanitizeAspect(srcValues.aspect),
      social: sanitizeSocial(srcValues.social),
    };

    return { pinnedChips, chipValues };
  } catch (err) {
    console.error('[useFilterBarConfig] Failed to parse persisted state:', err);
    return cloneDefaults();
  }
}

function cloneDefaults(): PersistedShape {
  return {
    pinnedChips: [...DEFAULT_PINNED_CHIPS],
    chipValues: {
      tags: { ...DEFAULT_CHIP_VALUES.tags },
      rating: { ...DEFAULT_CHIP_VALUES.rating },
      type: { ...DEFAULT_CHIP_VALUES.type },
      source: { ...DEFAULT_CHIP_VALUES.source, platforms: [] },
      ai_status: { ...DEFAULT_CHIP_VALUES.ai_status },
      date_added: { ...DEFAULT_CHIP_VALUES.date_added },
      duration: { ...DEFAULT_CHIP_VALUES.duration },
      aspect: { ...DEFAULT_CHIP_VALUES.aspect, buckets: [] },
      social: sanitizeSocial(DEFAULT_CHIP_VALUES.social),
    },
  };
}

function clampRating(v: unknown): number {
  if (typeof v !== 'number' || Number.isNaN(v)) return 0;
  if (v < 0) return 0;
  if (v > 5) return 5;
  return Math.floor(v);
}

function sanitizeDateAdded(
  v: Partial<ChipValuesMap['date_added']> | undefined,
): ChipValuesMap['date_added'] {
  const preset =
    typeof v?.preset === 'string' &&
    (DATE_PRESETS as ReadonlyArray<string>).includes(v.preset)
      ? (v.preset as DatePresetId)
      : null;
  const customAfter =
    typeof v?.customAfter === 'string' && ISO_DATE_RE.test(v.customAfter)
      ? v.customAfter
      : null;
  const customBefore =
    typeof v?.customBefore === 'string' && ISO_DATE_RE.test(v.customBefore)
      ? v.customBefore
      : null;
  return { preset, customAfter, customBefore };
}

function sanitizeNonNegativeInt(v: unknown): number | null {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) return null;
  return Math.floor(v);
}

function sanitizeDuration(
  v: Partial<ChipValuesMap['duration']> | undefined,
): ChipValuesMap['duration'] {
  const preset =
    typeof v?.preset === 'string' &&
    (DURATION_PRESETS as ReadonlyArray<string>).includes(v.preset)
      ? (v.preset as DurationPresetId)
      : null;
  return {
    preset,
    customMin: sanitizeNonNegativeInt(v?.customMin),
    customMax: sanitizeNonNegativeInt(v?.customMax),
  };
}

function sanitizeAspect(
  v: Partial<ChipValuesMap['aspect']> | undefined,
): ChipValuesMap['aspect'] {
  const allowed = new Set<string>(ASPECT_BUCKETS);
  const buckets = Array.isArray(v?.buckets)
    ? (v!.buckets.filter(
        (b) => typeof b === 'string' && allowed.has(b),
      ) as AspectBucketId[])
    : [];
  return { buckets };
}

function sanitizeSocial(
  v: Partial<SocialChipValue> | undefined,
): SocialChipValue {
  const defaults = DEFAULT_CHIP_VALUES.social;
  const combine = v?.combine === 'or' ? 'or' : 'and';
  const metricsSrc = (v?.metrics ?? {}) as Partial<SocialChipValue['metrics']>;
  const metrics: SocialChipValue['metrics'] = {
    likes: { ...defaults.metrics.likes },
    comments: { ...defaults.metrics.comments },
    favorites: { ...defaults.metrics.favorites },
    shares: { ...defaults.metrics.shares },
  };
  for (const key of SOCIAL_METRICS) {
    const src = metricsSrc[key];
    if (!src) continue;
    metrics[key] = {
      enabled: Boolean(src.enabled),
      threshold:
        typeof src.threshold === 'number' &&
        Number.isFinite(src.threshold) &&
        src.threshold >= 0
          ? Math.floor(src.threshold)
          : 0,
    };
  }
  return {
    combine,
    metrics,
    hasComments: Boolean(v?.hasComments),
  };
}

/** Detect whether a chip carries an active (non-default) value. */
function isChipActiveById(id: ChipId, values: ChipValuesMap): boolean {
  switch (id) {
    case 'tags':
      return values.tags.tag_ids.length > 0;
    case 'rating':
      return values.rating.min_rating > 0;
    case 'type':
      return values.type.types.length > 0;
    case 'source':
      return values.source.platforms.length > 0;
    case 'ai_status':
      return (
        values.ai_status.transcribed ||
        values.ai_status.summarized ||
        values.ai_status.analyzed
      );
    case 'date_added': {
      const d = values.date_added;
      if (!d.preset) return false;
      if (d.preset !== 'custom') return true;
      return Boolean(d.customAfter || d.customBefore);
    }
    case 'duration': {
      const d = values.duration;
      if (!d.preset) return false;
      if (d.preset !== 'custom') return true;
      return d.customMin != null || d.customMax != null;
    }
    case 'aspect':
      return values.aspect.buckets.length > 0;
    case 'social': {
      const s = values.social;
      if (s.hasComments) return true;
      return SOCIAL_METRICS.some((m) => s.metrics[m].enabled);
    }
    default:
      return false;
  }
}

export interface UseFilterBarConfigReturn {
  /** Ordered list of currently pinned chip ids. */
  pinnedChips: ChipId[];
  /** Chip ids NOT pinned (i.e. available to pin). */
  availableChips: ChipId[];
  /** Current values per chip. Keyed lookup, never mutate in place. */
  chipValues: ChipValuesMap;
  /** True iff any chip has a non-default value. */
  hasActiveFilters: boolean;
  /** Count of chips with active values (for summary badges). */
  activeFilterCount: number;

  pinChip: (id: ChipId) => void;
  unpinChip: (id: ChipId) => void;
  reorderChips: (id: ChipId, direction: 'up' | 'down') => void;

  setChipValue: <K extends ChipId>(id: K, value: ChipValuesMap[K]) => void;
  clearChip: (id: ChipId) => void;
  clearAll: () => void;

  isChipActive: (id: ChipId) => boolean;

  /** Build a server-shaped params object from the active chip values. */
  toFilterParams: () => FetchResourcesFilterParams;
}

export function useFilterBarConfig(): UseFilterBarConfigReturn {
  const [state, setState] = useState<PersistedShape>(() => loadFromStorage());

  // Persist on every change. Writing is rare (user-driven), so no debounce.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (err) {
      // Quota / disabled-storage shouldn't crash the UI.
      console.error('[useFilterBarConfig] Failed to persist state:', err);
    }
  }, [state]);

  const availableChips = useMemo<ChipId[]>(
    () => CHIP_IDS.filter((id) => !state.pinnedChips.includes(id)),
    [state.pinnedChips],
  );

  const isChipActive = useCallback(
    (id: ChipId) => isChipActiveById(id, state.chipValues),
    [state.chipValues],
  );

  const activeFilterCount = useMemo(
    () =>
      CHIP_IDS.reduce(
        (sum, id) => sum + (isChipActiveById(id, state.chipValues) ? 1 : 0),
        0,
      ),
    [state.chipValues],
  );

  const pinChip = useCallback((id: ChipId) => {
    setState((prev) => {
      if (prev.pinnedChips.includes(id)) return prev;
      return { ...prev, pinnedChips: [...prev.pinnedChips, id] };
    });
  }, []);

  const unpinChip = useCallback((id: ChipId) => {
    setState((prev) => {
      if (!prev.pinnedChips.includes(id)) return prev;
      return {
        ...prev,
        pinnedChips: prev.pinnedChips.filter((c) => c !== id),
      };
    });
  }, []);

  const reorderChips = useCallback(
    (id: ChipId, direction: 'up' | 'down') => {
      setState((prev) => {
        const idx = prev.pinnedChips.indexOf(id);
        if (idx === -1) return prev;
        const swapIdx = direction === 'up' ? idx - 1 : idx + 1;
        if (swapIdx < 0 || swapIdx >= prev.pinnedChips.length) return prev;
        const next = [...prev.pinnedChips];
        [next[idx], next[swapIdx]] = [next[swapIdx], next[idx]];
        return { ...prev, pinnedChips: next };
      });
    },
    [],
  );

  const setChipValue = useCallback(
    <K extends ChipId>(id: K, value: ChipValuesMap[K]) => {
      setState((prev) => ({
        ...prev,
        chipValues: { ...prev.chipValues, [id]: value },
      }));
    },
    [],
  );

  const clearChip = useCallback((id: ChipId) => {
    setState((prev) => ({
      ...prev,
      chipValues: { ...prev.chipValues, [id]: DEFAULT_CHIP_VALUES[id] },
    }));
  }, []);

  const clearAll = useCallback(() => {
    setState((prev) => ({ ...prev, chipValues: DEFAULT_CHIP_VALUES }));
  }, []);

  const toFilterParams = useCallback((): FetchResourcesFilterParams => {
    const out: FetchResourcesFilterParams = {};
    if (state.chipValues.tags.tag_ids.length > 0) {
      out.tag_ids = [...state.chipValues.tags.tag_ids];
    }
    if (state.chipValues.rating.min_rating > 0) {
      out.min_rating = state.chipValues.rating.min_rating;
    }
    if (state.chipValues.type.types.length > 0) {
      out.types = [...state.chipValues.type.types];
    }
    if (state.chipValues.source.platforms.length > 0) {
      out.platforms = [...state.chipValues.source.platforms];
    }
    if (state.chipValues.ai_status.transcribed) out.ai_transcribed = true;
    if (state.chipValues.ai_status.summarized) out.ai_summarized = true;
    if (state.chipValues.ai_status.analyzed) out.ai_analyzed = true;
    const range = datePresetToRange(state.chipValues.date_added);
    if (range.after) out.created_after = range.after;
    if (range.before) out.created_before = range.before;
    const durationRange = durationPresetToRange(state.chipValues.duration);
    if (durationRange.min != null) out.duration_min = durationRange.min;
    if (durationRange.max != null) out.duration_max = durationRange.max;
    if (state.chipValues.aspect.buckets.length > 0) {
      out.aspect_ratios = state.chipValues.aspect.buckets.map(bucketToApiValue);
    }
    const social = state.chipValues.social;
    const paramKey: Record<SocialMetric, keyof FetchResourcesFilterParams> = {
      likes: 'min_likes',
      comments: 'min_comments',
      favorites: 'min_favorites',
      shares: 'min_shares',
    };
    let anyEnabled = false;
    for (const m of SOCIAL_METRICS) {
      const entry = social.metrics[m];
      if (entry.enabled) {
        anyEnabled = true;
        // Cast is safe: every branch writes a number into the right key.
        (out[paramKey[m]] as number | undefined) = entry.threshold;
      }
    }
    if (anyEnabled) {
      out.social_combine = social.combine;
    }
    if (social.hasComments) {
      out.has_comments = true;
    }
    return out;
  }, [state.chipValues]);

  return {
    pinnedChips: state.pinnedChips,
    availableChips,
    chipValues: state.chipValues,
    hasActiveFilters: activeFilterCount > 0,
    activeFilterCount,
    pinChip,
    unpinChip,
    reorderChips,
    setChipValue,
    clearChip,
    clearAll,
    isChipActive,
    toFilterParams,
  };
}
