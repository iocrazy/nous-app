// frontend/components/resources/filter/types.ts
//
// Type contracts for the Eagle-style pinnable filter bar (PR 3 — tags /
// rating / type / source / ai_status / date_added / duration / aspect).
// Adding a new chip means: (1) add its id to ChipId, (2) add its default
// value to DEFAULT_CHIP_VALUES, (3) add a reducer case in
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
  'duration',
  'aspect',
  'social',
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

/** Video-specific duration bucket presets + custom absolute window. */
export type DurationPresetId =
  | 'short60s' // ≤ 60 seconds
  | 'medium' // 1-5 minutes (60..300)
  | 'long' // 5-30 minutes (300..1800)
  | 'xlong' // 30+ minutes (1800..Infinity)
  | 'custom';

export interface DurationChipValue {
  /** null = inactive. */
  preset: DurationPresetId | null;
  /** Only consulted when preset === 'custom'. Seconds, non-negative ints. */
  customMin: number | null;
  customMax: number | null;
}

/** Aspect-ratio bucket ids. ``other`` catches anything that doesn't
 *  fall into the four named buckets (or has no parseable resolution). */
export type AspectBucketId =
  | 'portrait' // 9:16-ish (ratio ∈ (0.5, 0.6))
  | 'landscape' // 16:9-ish (ratio ∈ (1.7, 1.85))
  | 'square' // 1:1-ish  (ratio ∈ (0.95, 1.05))
  | 'fourThree' // 4:3-ish  (ratio ∈ (1.28, 1.4))
  | 'other';

/** Multi-select aspect buckets (OR semantics — match any). */
export interface AspectChipValue {
  /** Empty array means "no aspect filter applied". */
  buckets: AspectBucketId[];
}

/** Social metric fields exposed by the chip — each tracks whether the
 *  user has enabled the threshold plus the minimum value they chose. */
export type SocialMetric = 'likes' | 'comments' | 'favorites' | 'shares';

/** Per-metric threshold entry. ``enabled`` gates the ``threshold``;
 *  disabled thresholds are never applied even if carrying a value. */
export interface SocialMetricThreshold {
  enabled: boolean;
  /** Inclusive ``>=`` bound. ``0`` is valid and distinct from inactive. */
  threshold: number;
}

/** Chip state for the Social (interaction-data) filter. */
export interface SocialChipValue {
  /** Combine semantics across *enabled* metric thresholds. */
  combine: 'and' | 'or';
  /** Per-metric thresholds. Order is display-only, set via SOCIAL_METRICS. */
  metrics: Record<SocialMetric, SocialMetricThreshold>;
  /** Independent "has at least one comment" flag — applied with AND on
   *  top of the combined thresholds (so it's a hard floor, not a
   *  substitute for comments >= N). */
  hasComments: boolean;
}

/** Display order for the social metric rows. */
export const SOCIAL_METRICS: readonly SocialMetric[] = [
  'likes',
  'comments',
  'favorites',
  'shares',
] as const;

export type ChipValue =
  | TagsChipValue
  | RatingChipValue
  | TypeChipValue
  | SourceChipValue
  | AIStatusChipValue
  | DateAddedChipValue
  | DurationChipValue
  | AspectChipValue
  | SocialChipValue;

/** Discriminated lookup so consumers can narrow by chip id. */
export interface ChipValuesMap {
  tags: TagsChipValue;
  rating: RatingChipValue;
  type: TypeChipValue;
  source: SourceChipValue;
  ai_status: AIStatusChipValue;
  date_added: DateAddedChipValue;
  duration: DurationChipValue;
  aspect: AspectChipValue;
  social: SocialChipValue;
}

/** The inactive state for each chip — used for reset. */
export const DEFAULT_CHIP_VALUES: ChipValuesMap = {
  tags: { tag_ids: [] },
  rating: { min_rating: 0 },
  type: { types: [] },
  source: { platforms: [] },
  ai_status: { transcribed: false, summarized: false, analyzed: false },
  date_added: { preset: null, customAfter: null, customBefore: null },
  duration: { preset: null, customMin: null, customMax: null },
  aspect: { buckets: [] },
  social: {
    combine: 'and',
    metrics: {
      likes: { enabled: false, threshold: 0 },
      comments: { enabled: false, threshold: 0 },
      favorites: { enabled: false, threshold: 0 },
      shares: { enabled: false, threshold: 0 },
    },
    hasComments: false,
  },
};

/** Default pin order (PR 2 baseline — 6 chips pinned). PR 3 adds
 *  ``duration`` and ``aspect`` to the catalog but leaves them unpinned;
 *  users opt in via the Filter config panel. */
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
 * params added in PR 2 + PR 3.
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
  duration_min?: number; // seconds
  duration_max?: number; // seconds
  aspect_ratios?: string[]; // "9:16" / "16:9" / "1:1" / "4:3" / "other"
  min_likes?: number;
  min_comments?: number;
  min_favorites?: number;
  min_shares?: number;
  social_combine?: 'and' | 'or';
  has_comments?: boolean;
}
