// frontend/components/resources/filter/types.ts
//
// Type contracts for the Eagle-style pinnable filter bar (PR 2 — tags /
// rating / type / source / ai_status / date_added). Adding a new chip
// means: (1) add its id to ChipId, (2) add its default value to
// DEFAULT_CHIP_VALUES, (3) add a reducer case in
// useFilterBarConfig.isChipActive, (4) write a dropdown, (5) register
// it in FilterBar's renderDropdown + chipLabel + chipSummary.

import type { ResourceFilterType } from '../resourceFilters';

/** The canonical ordered list of supported chip ids. */
export const CHIP_IDS = [
  'tags',
  'rating',
  'type',
  'source',
  'ai_status',
  'date_added',
] as const;
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

/** Multi-select platforms (OR semantics — match any). */
export interface SourceChipValue {
  /** Empty array means "no source filter applied".
   *  Values mirror parsed_media.source_platform: douyin / xiaohongshu /
   *  bilibili / youtube / tiktok / twitter / other / upload / ... */
  platforms: string[];
}

/**
 * Three independent AI status flags (AND semantics — every flag set to
 * true must be satisfied). Inactive == all three false.
 */
export interface AIStatusChipValue {
  transcribed: boolean;
  summarized: boolean;
  analyzed: boolean;
}

/** Preset relative ranges + custom absolute window. */
export type DatePresetId =
  | 'today'
  | 'thisWeek'
  | 'thisMonth'
  | 'last30days'
  | 'last90days'
  | 'custom';

export interface DateAddedChipValue {
  /** null = inactive. */
  preset: DatePresetId | null;
  /** Only consulted when preset === 'custom'. ISO date strings (YYYY-MM-DD). */
  customAfter: string | null;
  customBefore: string | null;
}

export type ChipValue =
  | TagsChipValue
  | RatingChipValue
  | TypeChipValue
  | SourceChipValue
  | AIStatusChipValue
  | DateAddedChipValue;

/** Discriminated lookup so consumers can narrow by chip id. */
export interface ChipValuesMap {
  tags: TagsChipValue;
  rating: RatingChipValue;
  type: TypeChipValue;
  source: SourceChipValue;
  ai_status: AIStatusChipValue;
  date_added: DateAddedChipValue;
}

/** The inactive state for each chip — used for reset. */
export const DEFAULT_CHIP_VALUES: ChipValuesMap = {
  tags: { tag_ids: [] },
  rating: { min_rating: 0 },
  type: { types: [] },
  source: { platforms: [] },
  ai_status: { transcribed: false, summarized: false, analyzed: false },
  date_added: { preset: null, customAfter: null, customBefore: null },
};

/** Default pin order (Eagle-style PR 2 baseline — all 6 chips pinned). */
export const DEFAULT_PINNED_CHIPS: ChipId[] = [
  'tags',
  'rating',
  'type',
  'source',
  'ai_status',
  'date_added',
];

/**
 * Parameters the filter bar contributes to a resource-list API call.
 * Kept separate from internal chip state so the backend contract and
 * the UI can evolve independently. Mirrors the new /resources query
 * params added in PR 2.
 */
export interface FetchResourcesFilterParams {
  tag_ids?: string[];
  min_rating?: number;
  types?: ResourceFilterType[];
  platforms?: string[];
  ai_transcribed?: boolean;
  ai_summarized?: boolean;
  ai_analyzed?: boolean;
  created_after?: string; // YYYY-MM-DD
  created_before?: string; // YYYY-MM-DD
}
