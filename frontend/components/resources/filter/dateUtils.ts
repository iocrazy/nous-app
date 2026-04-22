// frontend/components/resources/filter/dateUtils.ts
//
// Small pure helpers for the Date-added filter chip. Keeps preset →
// ISO-range resolution in one place so both the dropdown summary and
// useFilterBarConfig.toFilterParams() agree.

import type { DateAddedChipValue, DatePresetId } from './types';

/** YYYY-MM-DD from a local-calendar Date. */
function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function daysAgo(n: number): Date {
  const d = startOfDay(new Date());
  d.setDate(d.getDate() - n);
  return d;
}

function startOfThisWeek(): Date {
  // ISO week starting Monday. Local calendar.
  const today = startOfDay(new Date());
  const dow = today.getDay(); // 0 = Sun .. 6 = Sat
  const diff = dow === 0 ? 6 : dow - 1;
  const monday = new Date(today);
  monday.setDate(today.getDate() - diff);
  return monday;
}

function startOfThisMonth(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), 1);
}

export interface DateRangeStrings {
  /** Inclusive lower bound as YYYY-MM-DD, or null. */
  after: string | null;
  /** Inclusive upper bound as YYYY-MM-DD, or null. */
  before: string | null;
}

export function datePresetToRange(value: DateAddedChipValue): DateRangeStrings {
  if (!value.preset) return { after: null, before: null };
  const today = toIsoDate(startOfDay(new Date()));
  switch (value.preset) {
    case 'today':
      return { after: today, before: today };
    case 'thisWeek':
      return { after: toIsoDate(startOfThisWeek()), before: today };
    case 'thisMonth':
      return { after: toIsoDate(startOfThisMonth()), before: today };
    case 'last30days':
      return { after: toIsoDate(daysAgo(29)), before: today };
    case 'last90days':
      return { after: toIsoDate(daysAgo(89)), before: today };
    case 'custom':
      return {
        after: value.customAfter,
        before: value.customBefore,
      };
    default:
      return { after: null, before: null };
  }
}

/** Build the chip summary text shown next to the label. Returns null
 *  if the chip is inactive.
 */
export function datePresetSummary(
  value: DateAddedChipValue,
  labels: Record<DatePresetId, string>,
): string | null {
  if (!value.preset) return null;
  if (value.preset !== 'custom') return labels[value.preset];
  const a = value.customAfter;
  const b = value.customBefore;
  if (!a && !b) return null;
  if (a && b) return `${a} ~ ${b}`;
  if (a) return `≥ ${a}`;
  return `≤ ${b}`;
}
