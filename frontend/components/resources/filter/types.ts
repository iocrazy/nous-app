// frontend/components/resources/filter/types.ts
//
// Type contracts for the Eagle-style pinnable filter bar (PR 1 — tags /
// rating / type). Adding a new chip means: (1) add its id to ChipId,
// (2) add its default value to DEFAULT_CHIP_VALUES, (3) add a reducer
// case in useFilterBarConfig.isChipActive, (4) write a dropdown.

import type { ResourceFilterType } from '../resourceFilters';

/** The canonical ordered list of supported chip ids. */
export const CHIP_IDS = ['tags', 'rating', 'type'] as const;
export type ChipId = (typeof CHIP_IDS)[number];

/** Per-chip value shape. New chips extend this union. */
export interface TagsChipValue {
  tag_ids: string[]; // UUID strings; AND semantics
}

export interface RatingChipValue {
  /** 0 means "no minimum" / inactive. 1..5 means "rating >= N stars". */
  min_rating: number;
}

export interface TypeChipValue {
  /** Empty array means "no type filter applied". */
  types: ResourceFilterType[];
}

export type ChipValue = TagsChipValue | RatingChipValue | TypeChipValue;

/** Discriminated lookup so consumers can narrow by chip id. */
export interface ChipValuesMap {
  tags: TagsChipValue;
  rating: RatingChipValue;
  type: TypeChipValue;
}

/** The inactive state for each chip — used for reset. */
export const DEFAULT_CHIP_VALUES: ChipValuesMap = {
  tags: { tag_ids: [] },
  rating: { min_rating: 0 },
  type: { types: [] },
};

/** Default pin order (Eagle-style PR 1 baseline). */
export const DEFAULT_PINNED_CHIPS: ChipId[] = ['tags', 'rating', 'type'];

/**
 * Parameters the filter bar contributes to a resource-list API call.
 * Kept separate from internal chip state so the backend contract and
 * the UI can evolve independently.
 */
export interface FetchResourcesFilterParams {
  tag_ids?: string[];
  min_rating?: number;
  types?: ResourceFilterType[];
}
