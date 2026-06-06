// frontend/components/filters/facetMeta.ts
//
// Shared metadata for the Pixcall-style filter chip bar + per-facet pickers.
// One source of truth for the dimension list, option tables, and the
// active-value label rendered on an applied chip. Consumed by FilterChipBar
// and FacetPickerSheet so both views (Downloads + Resources) stay consistent.

import {
  Tag as TagIcon,
  FileType,
  Globe,
  Sparkles,
  Calendar,
  Heart,
  Star,
  RectangleHorizontal,
  Clock,
  type LucideIcon,
} from 'lucide-react';

import type { ChipId, ChipValuesMap } from '../resources/filter/types';
import { SOCIAL_METRICS } from '../resources/filter/types';
import type { ResourceFilterType } from '../resources/resourceFilters';
import type {
  AspectBucketId,
  DatePresetId,
  DurationPresetId,
  SocialMetric,
} from '../resources/filter/types';
import type { Tag } from '../../types';

export interface FacetDef {
  id: ChipId;
  label: string;
  icon: LucideIcon;
}

/** Chip-bar order: most-useful first. Active facets get hoisted ahead of
 *  inactive ones at render time by FilterChipBar. */
export const FACETS: readonly FacetDef[] = [
  { id: 'tags', label: 'Tag', icon: TagIcon },
  { id: 'type', label: 'Type', icon: FileType },
  { id: 'source', label: 'Source', icon: Globe },
  { id: 'ai_status', label: 'AI', icon: Sparkles },
  { id: 'date_added', label: 'Date', icon: Calendar },
  { id: 'social', label: 'Social', icon: Heart },
  { id: 'rating', label: 'Rating', icon: Star },
  { id: 'aspect', label: 'Aspect', icon: RectangleHorizontal },
  { id: 'duration', label: 'Duration', icon: Clock },
];

// ─── Option tables (labels English per UI-language rule) ────────────────────

export const TYPE_OPTIONS: ReadonlyArray<{ id: ResourceFilterType; label: string }> = [
  { id: 'video', label: 'Video' },
  { id: 'image', label: 'Image' },
  { id: 'audio', label: 'Audio' },
  { id: 'document', label: 'Document' },
  { id: 'other', label: 'Other' },
];

export const KNOWN_PLATFORMS: readonly string[] = [
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
export function platformLabel(p: string): string {
  return PLATFORM_LABELS[p] ?? p.charAt(0).toUpperCase() + p.slice(1);
}

export const AI_FLAGS: ReadonlyArray<{
  key: 'transcribed' | 'summarized' | 'analyzed';
  label: string;
}> = [
  { key: 'transcribed', label: 'Transcribed' },
  { key: 'summarized', label: 'Summarized' },
  { key: 'analyzed', label: 'Analyzed' },
];

export const DATE_PRESETS: ReadonlyArray<{ id: DatePresetId; label: string }> = [
  { id: 'today', label: 'Today' },
  { id: 'thisWeek', label: 'This week' },
  { id: 'thisMonth', label: 'This month' },
  { id: 'last30days', label: 'Last 30 days' },
  { id: 'last90days', label: 'Last 90 days' },
];

export const DURATION_PRESETS: ReadonlyArray<{ id: DurationPresetId; label: string }> = [
  { id: 'short60s', label: '≤ 60s' },
  { id: 'medium', label: '1–5 min' },
  { id: 'long', label: '5–30 min' },
  { id: 'xlong', label: '30 min+' },
];

export const ASPECT_OPTIONS: ReadonlyArray<{ id: AspectBucketId; label: string }> = [
  { id: 'portrait', label: '9:16' },
  { id: 'landscape', label: '16:9' },
  { id: 'square', label: '1:1' },
  { id: 'fourThree', label: '4:3' },
  { id: 'other', label: 'Other' },
];

export const SOCIAL_LABELS: Record<SocialMetric, string> = {
  likes: 'Likes',
  comments: 'Comments',
  favorites: 'Favorites',
  shares: 'Shares',
};

// ─── Active-chip value label ────────────────────────────────────────────────

/**
 * Short label shown on an APPLIED chip. Single value → the value itself
 * (e.g. "Video", a tag name, "This week"); multi → "<Dim> · N". Mirrors the
 * Pixcall behaviour where an applied tag chip reads "abc", not "Tag: abc".
 */
export function facetActiveLabel(
  id: ChipId,
  cv: ChipValuesMap,
  allTags: Tag[],
): string {
  switch (id) {
    case 'tags': {
      const ids = cv.tags.tag_ids;
      if (ids.length === 1) {
        return allTags.find((t) => t.id === ids[0])?.name ?? 'Tag';
      }
      return `Tag · ${ids.length}`;
    }
    case 'type': {
      const ts = cv.type.types;
      if (ts.length === 1) {
        return TYPE_OPTIONS.find((o) => o.id === ts[0])?.label ?? 'Type';
      }
      return `Type · ${ts.length}`;
    }
    case 'source': {
      const ps = cv.source.platforms;
      return ps.length === 1 ? platformLabel(ps[0]) : `Source · ${ps.length}`;
    }
    case 'ai_status': {
      const flags = AI_FLAGS.filter((f) => cv.ai_status[f.key]);
      return flags.length === 1 ? flags[0].label : `AI · ${flags.length}`;
    }
    case 'date_added':
      return (
        DATE_PRESETS.find((d) => d.id === cv.date_added.preset)?.label ?? 'Date'
      );
    case 'duration':
      return (
        DURATION_PRESETS.find((d) => d.id === cv.duration.preset)?.label ??
        'Duration'
      );
    case 'aspect': {
      const bs = cv.aspect.buckets;
      if (bs.length === 1) {
        return ASPECT_OPTIONS.find((o) => o.id === bs[0])?.label ?? 'Aspect';
      }
      return `Aspect · ${bs.length}`;
    }
    case 'rating':
      return `≥ ${cv.rating.min_rating}★`;
    case 'social': {
      const n =
        SOCIAL_METRICS.filter((m) => cv.social.metrics[m].enabled).length +
        (cv.social.hasComments ? 1 : 0);
      return `Social · ${n}`;
    }
    default:
      return '';
  }
}
