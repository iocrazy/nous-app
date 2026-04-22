// frontend/components/resources/filter/aspectUtils.ts
//
// Pure helpers for the Aspect filter chip. Resource resolution is
// stored as a "WIDTHxHEIGHT" string (e.g. "1920x1080") so we parse it
// here and map the ratio to one of the named buckets.

import type { AspectBucketId, AspectChipValue } from './types';

/** Ratio bucket definitions. ``other`` is implicit — anything not
 *  matching the four named ranges falls into it. */
const BUCKET_RANGES: Record<
  Exclude<AspectBucketId, 'other'>,
  { min: number; max: number }
> = {
  portrait: { min: 0.5, max: 0.6 }, // 9:16 ≈ 0.5625
  landscape: { min: 1.7, max: 1.85 }, // 16:9 ≈ 1.7778
  square: { min: 0.95, max: 1.05 }, // 1:1
  fourThree: { min: 1.28, max: 1.4 }, // 4:3 ≈ 1.3333
};

/** Parse a "WIDTHxHEIGHT" string into a numeric ratio (``width /
 *  height``). Returns null for missing / malformed / zero-height
 *  inputs. */
export function parseAspectRatio(
  resolution: string | null | undefined,
): number | null {
  if (!resolution || typeof resolution !== 'string') return null;
  const match = resolution.trim().match(/^(\d+)\s*[xX×]\s*(\d+)$/);
  if (!match) return null;
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  if (width <= 0 || height <= 0) return null;
  return width / height;
}

/** Classify a ratio into its bucket id. */
export function ratioToBucket(ratio: number | null): AspectBucketId | null {
  if (ratio == null || !Number.isFinite(ratio)) return null;
  for (const [bucket, range] of Object.entries(BUCKET_RANGES)) {
    if (ratio > range.min && ratio < range.max) {
      return bucket as AspectBucketId;
    }
  }
  return 'other';
}

/** Does a resource's resolution match any of the selected buckets?
 *  Returns true when no buckets are selected (chip inactive). */
export function aspectMatches(
  resolution: string | null | undefined,
  buckets: AspectBucketId[],
): boolean {
  if (buckets.length === 0) return true;
  const ratio = parseAspectRatio(resolution);
  const bucket = ratioToBucket(ratio);
  if (bucket == null) {
    // No parseable resolution → only match if the user explicitly
    // selected the ``other`` bucket. This is debatable; we lean
    // strict here so the filter is honest.
    return buckets.includes('other');
  }
  return buckets.includes(bucket);
}

/** Short chip summary ("9:16" / "9:16, 16:9" / "3"). */
const BUCKET_SHORT_LABELS: Record<AspectBucketId, string> = {
  portrait: '9:16',
  landscape: '16:9',
  square: '1:1',
  fourThree: '4:3',
  other: 'Other',
};

export function aspectSummary(value: AspectChipValue): string | null {
  const buckets = value.buckets;
  if (buckets.length === 0) return null;
  if (buckets.length <= 2) {
    return buckets.map((b) => BUCKET_SHORT_LABELS[b]).join(', ');
  }
  return String(buckets.length);
}

/** Map a client-side bucket id to the string the backend accepts on
 *  the ``aspect_ratios`` query param. */
export function bucketToApiValue(bucket: AspectBucketId): string {
  switch (bucket) {
    case 'portrait':
      return '9:16';
    case 'landscape':
      return '16:9';
    case 'square':
      return '1:1';
    case 'fourThree':
      return '4:3';
    case 'other':
      return 'other';
  }
}
