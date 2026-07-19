/**
 * Pure helpers for the Team AI Usage panel (W3c) — extracted so the number
 * formatting, date-range math, and chart pivot can be unit-tested without a DOM.
 */

import type { UsageDailyRow } from '../services/usageService';

export type RangePreset = '7d' | '30d' | 'month';

/** Compact token count, e.g. 12345 → "12.3k", 2_100_000 → "2.1M". */
export function formatTokens(n: number): string {
  const v = Math.max(0, Math.round(n || 0));
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return String(v);
}

/** cents → USD string. 42 → "$0.42", 1234 → "$12.34". */
export function formatCentsAsUsd(cents: number): string {
  const dollars = (cents || 0) / 100;
  return `$${dollars.toFixed(2)}`;
}

/** Compact one-line cost summary for the issue detail strip. */
export function formatIssueCostLine(totalTokens: number, costCents: number): string {
  const tokens = formatTokens(totalTokens);
  // Only show a price when something was actually priced (> 0).
  if (costCents > 0) {
    return `AI cost: ${tokens} tokens · ${formatCentsAsUsd(costCents)}`;
  }
  return `AI cost: ${tokens} tokens`;
}

/**
 * Resolve a preset to a [from, to) ISO window (UTC). `to` is exclusive: the
 * start of tomorrow, so today's usage is included.
 */
export function presetRange(
  preset: RangePreset,
  now: Date = new Date(),
): { from: string; to: string } {
  const to = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1),
  );
  let from: Date;
  if (preset === 'month') {
    from = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  } else {
    const days = preset === '7d' ? 7 : 30;
    from = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - days + 1),
    );
  }
  return { from: from.toISOString(), to: to.toISOString() };
}

const NULL_KEY = '__none__';

/**
 * Pivot the flat (day, key, metric) rows into one object per day with a column
 * per series key — the shape Recharts stacked charts want. Null keys collapse
 * into a stable '__none__' column. `metric` picks tokens or cost.
 */
export function pivotDaily(
  daily: UsageDailyRow[],
  metric: 'total_tokens' | 'cost_cents',
): { rows: Array<Record<string, number | string>>; keys: string[] } {
  const byDay = new Map<string, Record<string, number | string>>();
  const keySet = new Set<string>();

  for (const row of daily) {
    const day = row.day;
    const key = row.key ?? NULL_KEY;
    keySet.add(key);
    let bucket = byDay.get(day);
    if (!bucket) {
      bucket = { day };
      byDay.set(day, bucket);
    }
    const raw = metric === 'cost_cents' ? (row.cost_cents || 0) / 100 : row.total_tokens || 0;
    bucket[key] = ((bucket[key] as number) || 0) + raw;
  }

  const rows = Array.from(byDay.values()).sort((a, b) =>
    String(a.day).localeCompare(String(b.day)),
  );
  const keys = Array.from(keySet);
  return { rows, keys };
}

/** Human label for a group key given the active grouping. */
export function groupLabel(
  key: string | null,
  groupBy: string,
  agentNames: Record<string, string> = {},
): string {
  if (key === null || key === NULL_KEY) {
    return groupBy === 'attribution' ? 'Unattributed' : 'None';
  }
  if (groupBy === 'agent') return agentNames[key] || key;
  if (groupBy === 'attribution') {
    if (key === 'direct_human') return 'Human';
    if (key === 'rule_owner') return 'Automation';
  }
  return key;
}

export { NULL_KEY };
