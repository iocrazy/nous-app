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

/**
 * 每件产出的花费（3c §3.3）。比率由**服务端**算一次：0 件产出时后端发的是
 * `null`（不知道单价），不是 0.0 —— 渲成 `¢0.00` 等于宣布产出是免费的。所以这里
 * 只负责把 null 说成「—」，绝不自己拿 cost/deliverables 再除一次。
 */
export function formatCostPerOutput(
  total: { cost_per_deliverable_cents: number | null } | null | undefined,
): string {
  const cents = total?.cost_per_deliverable_cents;
  return cents == null ? '—' : `¢${cents.toFixed(2)}`;
}

/**
 * 工具调用的错误率（3c §3.3）。
 *
 * 分母为 0 时**不是** 0.0%：一次工具都没调过的窗口没有错误率，说成 0.0% 会被读成
 * 「工具很稳」。反过来，调过 40 次、0 次出错是一条确定的好消息，必须是 0.0% 而不
 * 是「—」。这两句话是同一个格子里的两种真相。
 */
export function formatToolErrorRate(
  total: { tool_calls: number; tool_errors: number } | null | undefined,
): string {
  const calls = total?.tool_calls ?? 0;
  if (calls <= 0) return '—';
  return `${(((total?.tool_errors ?? 0) / calls) * 100).toFixed(1)}%`;
}

/**
 * 用量页当前展示的窗口，ISO 两端（修复轮 1 #2）。
 *
 * 复刻 `/usage/daily` 的窗口语义，好让效率读面和日汇总问的是**同一段时间**：
 * 给了 `month` 就是那个自然月 `[月初, 下月初)`（与后端 `_month_bounds` 同口径，
 * 上界独占），否则是 `[now - days, now]`。
 *
 * 不带窗口去问效率端点会拿到后端默认的 30 天：用户把范围切到 90d 之后，页面上
 * 四格说的是 90 天、两格说的是 30 天，而屏幕上没有任何东西提示这件事。
 *
 * 解析不了的 month 退回 days 预设——宁可问一个能答的窗口，也不要发一个
 * `invalid_range` 出去。
 */
export function usageWindow(
  opts: { days: number; month?: string },
  now: Date = new Date(),
): { from: string; to: string } {
  const m = opts.month ? /^(\d{4})-(\d{2})$/.exec(opts.month) : null;
  if (m) {
    const year = Number(m[1]);
    const month = Number(m[2]);
    if (month >= 1 && month <= 12) {
      return {
        from: new Date(Date.UTC(year, month - 1, 1)).toISOString(),
        to: new Date(Date.UTC(year, month, 1)).toISOString(),
      };
    }
  }
  const to = now;
  const from = new Date(to.getTime() - opts.days * 24 * 3600 * 1000);
  return { from: from.toISOString(), to: to.toISOString() };
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
