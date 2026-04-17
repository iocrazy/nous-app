/**
 * Shared filter / sort option descriptors for ResourcesView and related
 * components. Extracted so the toolbar UI can be reimplemented (or lifted
 * into its own component) without duplicating the label lookups.
 *
 * The actual translations come from the i18n bundle at render time; this
 * module only holds the type + value list.
 */

import type { TFunction } from 'i18next';
import type { SortBy } from '../../contexts/ResourcesContext';

export type ResourceFilterType =
  | 'video'
  | 'image'
  | 'audio'
  | 'document'
  | 'other';

export interface FilterOption {
  value: ResourceFilterType;
  label: string;
}

export interface SortOption {
  value: SortBy;
  label: string;
}

/** Build localized filter options using a react-i18next `t` function. */
export function buildFilterOptions(
  t: TFunction,
): readonly FilterOption[] {
  return [
    { value: 'video', label: t('smartFolder.fileTypes.video') },
    { value: 'image', label: t('smartFolder.fileTypes.image') },
    { value: 'audio', label: t('smartFolder.fileTypes.audio') },
    { value: 'document', label: t('smartFolder.fileTypes.document') },
    { value: 'other', label: t('smartFolder.fileTypes.other') },
  ];
}

/** Build localized sort options using a react-i18next `t` function. */
export function buildSortOptions(t: TFunction): readonly SortOption[] {
  return [
    { value: 'newest', label: t('resources.sortNewest') },
    { value: 'oldest', label: t('resources.sortOldest') },
    { value: 'name-az', label: t('resources.sortNameAZ') },
    { value: 'name-za', label: t('resources.sortNameZA') },
    { value: 'largest', label: t('resources.sortLargest') },
    { value: 'smallest', label: t('resources.sortSmallest') },
  ];
}

/**
 * Immutably toggle a filter in a Set (factored out of the component so
 * the behavior is testable in isolation).
 */
export function toggleFilterIn(
  current: Set<ResourceFilterType>,
  type: ResourceFilterType,
): Set<ResourceFilterType> {
  const next = new Set(current);
  if (next.has(type)) {
    next.delete(type);
  } else {
    next.add(type);
  }
  return next;
}

export const SORT_VALUES: readonly SortBy[] = [
  'newest',
  'oldest',
  'name-az',
  'name-za',
  'largest',
  'smallest',
];

export const FILTER_VALUES: readonly ResourceFilterType[] = [
  'video',
  'image',
  'audio',
  'document',
  'other',
];
