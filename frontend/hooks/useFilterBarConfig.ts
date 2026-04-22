// frontend/hooks/useFilterBarConfig.ts
//
// Persistent configuration + values for the Resources pinnable filter bar.
// State is kept entirely in-memory and mirrored to localStorage under
// `resourceFilterConfig`. The hook API is intentionally URL-friendly:
// every mutation returns via immutable copies so a future PR can sync
// `chipValues` to / from URL query params without reshaping the hook.

import { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  ChipId,
  ChipValuesMap,
  FetchResourcesFilterParams,
} from '../components/resources/filter/types';
import {
  CHIP_IDS,
  DEFAULT_CHIP_VALUES,
  DEFAULT_PINNED_CHIPS,
} from '../components/resources/filter/types';

const STORAGE_KEY = 'resourceFilterConfig';

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
  if (typeof window === 'undefined') return { ...DEFAULT_STATE };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_STATE };
    const parsed = JSON.parse(raw) as Partial<PersistedShape> | null;
    if (!parsed || typeof parsed !== 'object') return { ...DEFAULT_STATE };

    const validIds = new Set<ChipId>(CHIP_IDS);
    const pinnedChips = Array.isArray(parsed.pinnedChips)
      ? (parsed.pinnedChips.filter((id): id is ChipId =>
          typeof id === 'string' && validIds.has(id as ChipId),
        ) as ChipId[])
      : [...DEFAULT_PINNED_CHIPS];

    const chipValues: ChipValuesMap = {
      tags: {
        tag_ids: Array.isArray(parsed.chipValues?.tags?.tag_ids)
          ? (parsed.chipValues!.tags!.tag_ids.filter(
              (id) => typeof id === 'string',
            ) as string[])
          : [],
      },
      rating: {
        min_rating: clampRating(parsed.chipValues?.rating?.min_rating),
      },
      type: {
        types: Array.isArray(parsed.chipValues?.type?.types)
          ? (parsed.chipValues!.type!.types.filter(
              (t) =>
                t === 'video' ||
                t === 'image' ||
                t === 'audio' ||
                t === 'document' ||
                t === 'other',
            ) as ChipValuesMap['type']['types'])
          : [],
      },
    };

    return { pinnedChips, chipValues };
  } catch (err) {
    console.error('[useFilterBarConfig] Failed to parse persisted state:', err);
    return { ...DEFAULT_STATE };
  }
}

function clampRating(v: unknown): number {
  if (typeof v !== 'number' || Number.isNaN(v)) return 0;
  if (v < 0) return 0;
  if (v > 5) return 5;
  return Math.floor(v);
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
    () => CHIP_IDS.reduce((sum, id) => sum + (isChipActiveById(id, state.chipValues) ? 1 : 0), 0),
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
