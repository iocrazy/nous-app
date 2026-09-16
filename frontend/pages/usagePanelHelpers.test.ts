import { describe, expect, it } from 'vitest';

import {
  formatCentsAsUsd,
  formatCostPerOutput,
  formatIssueCostLine,
  formatTokens,
  formatToolErrorRate,
  groupLabel,
  pivotDaily,
  presetRange,
} from './usagePanelHelpers';
import type { UsageDailyRow } from '../services/usageService';

describe('formatTokens', () => {
  it('formats magnitudes compactly', () => {
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(999)).toBe('999');
    expect(formatTokens(12345)).toBe('12.3k');
    expect(formatTokens(2_100_000)).toBe('2.1M');
  });
  it('guards NaN/negative', () => {
    expect(formatTokens(NaN as unknown as number)).toBe('0');
    expect(formatTokens(-5)).toBe('0');
  });
});

describe('formatCentsAsUsd', () => {
  it('renders cents as dollars', () => {
    expect(formatCentsAsUsd(42)).toBe('$0.42');
    expect(formatCentsAsUsd(1234)).toBe('$12.34');
    expect(formatCentsAsUsd(0)).toBe('$0.00');
  });
});

describe('formatIssueCostLine', () => {
  it('includes price when priced', () => {
    expect(formatIssueCostLine(12300, 42)).toBe('AI cost: 12.3k tokens · $0.42');
  });
  it('omits price when unpriced (0)', () => {
    expect(formatIssueCostLine(500, 0)).toBe('AI cost: 500 tokens');
  });
});

describe('presetRange', () => {
  const now = new Date('2026-07-18T10:30:00Z');
  it('7d spans 7 days ending tomorrow-exclusive', () => {
    const { from, to } = presetRange('7d', now);
    expect(from).toBe('2026-07-12T00:00:00.000Z');
    expect(to).toBe('2026-07-19T00:00:00.000Z');
  });
  it('month starts at the 1st', () => {
    const { from, to } = presetRange('month', now);
    expect(from).toBe('2026-07-01T00:00:00.000Z');
    expect(to).toBe('2026-07-19T00:00:00.000Z');
  });
});

// `UsageDailyRow` 从 Task 10 起带上效率五列（后端恒发，缺省 0），这里补齐只是让
// 夹具保持真实 wire 形状——pivotDaily 本身不读它们。
const dailyRow = (over: Partial<UsageDailyRow> & { day: string }): UsageDailyRow => ({
  key: null,
  total_tokens: 0,
  cost_cents: 0,
  run_count: 0,
  failed_runs: 0,
  tool_calls: 0,
  tool_errors: 0,
  deliverables: 0,
  ...over,
});

describe('pivotDaily', () => {
  it('pivots rows into per-day objects with a column per key, cost→dollars', () => {
    const { rows, keys } = pivotDaily(
      [
        dailyRow({ day: '2026-07-17', key: 'qwen', total_tokens: 100, cost_cents: 150 }),
        dailyRow({ day: '2026-07-17', key: 'gpt', total_tokens: 50, cost_cents: 50 }),
        dailyRow({ day: '2026-07-18', key: 'qwen', total_tokens: 20, cost_cents: 25 }),
      ],
      'cost_cents',
    );
    expect(keys.sort()).toEqual(['gpt', 'qwen']);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ day: '2026-07-17', qwen: 1.5, gpt: 0.5 });
    expect(rows[1]).toMatchObject({ day: '2026-07-18', qwen: 0.25 });
  });
  it('collapses null keys into a stable column', () => {
    const { keys } = pivotDaily(
      [dailyRow({ day: '2026-07-18', key: null, total_tokens: 5 })],
      'total_tokens',
    );
    expect(keys).toEqual(['__none__']);
  });
});

describe('groupLabel', () => {
  it('maps attribution to human words', () => {
    expect(groupLabel('direct_human', 'attribution')).toBe('Human');
    expect(groupLabel('rule_owner', 'attribution')).toBe('Automation');
    expect(groupLabel(null, 'attribution')).toBe('Unattributed');
  });
  it('maps agent ids to names when available', () => {
    expect(groupLabel('a1', 'agent', { a1: 'Script AI' })).toBe('Script AI');
    expect(groupLabel('a1', 'agent', {})).toBe('a1');
  });
  it('passes model/module keys through', () => {
    expect(groupLabel('qwen-max', 'model')).toBe('qwen-max');
    expect(groupLabel(null, 'model')).toBe('None');
  });
});

// ─── 3c §3.3: the two efficiency tiles on the team panel ───────────────────
// 两个格子的全部难点都在分母是 0 的时候：一个是「不知道」，一个是确定的好消息。

describe('formatCostPerOutput', () => {
  it('prints the server-computed unit price', () => {
    expect(formatCostPerOutput({ cost_per_deliverable_cents: 5.0625 })).toBe('¢5.06');
  });

  it('says it does not know when nothing was produced', () => {
    // 后端 0 件产出发的是 null。渲成 ¢0.00 等于宣布「产出是免费的」。
    expect(formatCostPerOutput({ cost_per_deliverable_cents: null })).toBe('—');
    expect(formatCostPerOutput(null)).toBe('—');
  });

  it('keeps a genuine zero price as zero', () => {
    expect(formatCostPerOutput({ cost_per_deliverable_cents: 0 })).toBe('¢0.00');
  });
});

describe('formatToolErrorRate', () => {
  it('prints the share of calls that erred', () => {
    expect(formatToolErrorRate({ tool_calls: 40, tool_errors: 4 })).toBe('10.0%');
  });

  it('calls a clean window 0.0%, not unknown', () => {
    // 调过 40 次、0 次出错是一条确定的好消息。
    expect(formatToolErrorRate({ tool_calls: 40, tool_errors: 0 })).toBe('0.0%');
  });

  it('says it does not know when no tool was ever called', () => {
    expect(formatToolErrorRate({ tool_calls: 0, tool_errors: 0 })).toBe('—');
    expect(formatToolErrorRate(null)).toBe('—');
  });
});
