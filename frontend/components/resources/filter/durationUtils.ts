// frontend/components/resources/filter/durationUtils.ts
//
// Pure helpers for the Duration filter chip. Keeps preset → numeric
// range conversion in one place so both the dropdown summary, the
// client-side filter in useResourcesDisplay, and the server params
// built by useFilterBarConfig.toFilterParams() agree.

import type { DurationChipValue, DurationPresetId } from './types';

export interface DurationRange {
  /** Inclusive lower bound in seconds, or null. */
  min: number | null;
  /** Inclusive upper bound in seconds, or null. */
  max: number | null;
}

/** Preset ranges. ``short60s`` has no explicit lower bound; ``xlong``
 *  has no upper bound. */
const PRESET_RANGES: Record<Exclude<DurationPresetId, 'custom'>, DurationRange> = {
  short60s: { min: null, max: 60 },
  medium: { min: 60, max: 300 }, // 1-5 min
  long: { min: 300, max: 1800 }, // 5-30 min
  xlong: { min: 1800, max: null }, // 30+ min
};

export function durationPresetToRange(value: DurationChipValue): DurationRange {
  if (!value.preset) return { min: null, max: null };
  if (value.preset === 'custom') {
    return {
      min: value.customMin,
      max: value.customMax,
    };
  }
  return PRESET_RANGES[value.preset];
}

/** Format a seconds count as "Ns" for short clips or "Nm" for whole
 *  minutes, falling back to the raw seconds count otherwise. Used only
 *  in chip summaries — not precision-critical. */
function formatSeconds(s: number): string {
  if (s < 60) return `${s}s`;
  if (s % 60 === 0) return `${s / 60}m`;
  return `${s}s`;
}

/** Chip summary text. Returns null when the chip is inactive. */
export function durationPresetSummary(
  value: DurationChipValue,
  labels: Record<DurationPresetId, string>,
): string | null {
  if (!value.preset) return null;
  if (value.preset !== 'custom') return labels[value.preset];
  const { customMin, customMax } = value;
  if (customMin == null && customMax == null) return null;
  if (customMin != null && customMax != null) {
    return `${formatSeconds(customMin)} ~ ${formatSeconds(customMax)}`;
  }
  if (customMin != null) return `≥ ${formatSeconds(customMin)}`;
  return `≤ ${formatSeconds(customMax as number)}`;
}

/** Does a resource's duration (in seconds) fall within the selected
 *  range? ``null`` duration is excluded whenever a range is active —
 *  this chip is video-specific. */
export function durationMatches(
  durationSeconds: number | null | undefined,
  range: DurationRange,
): boolean {
  if (range.min == null && range.max == null) return true;
  if (durationSeconds == null || !Number.isFinite(durationSeconds)) return false;
  if (range.min != null && durationSeconds < range.min) return false;
  if (range.max != null && durationSeconds > range.max) return false;
  return true;
}
