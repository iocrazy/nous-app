/** 用量页 stat tiles（3c §3.3 / §6 稿三）。Success 改读 turn_end 分布：
 *  ``awaiting_input`` 是一个在等人的回合，算成失败等于告诉用户「越问越糟」。 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StatTiles } from './UsagePage';
import type { EfficiencySummary } from '../services/usageService';
import type { UsageDailySummary } from '../types';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, f?: string) => f ?? k }) }));
afterEach(cleanup);

const SUMMARY: UsageDailySummary = {
  days: 30, month: null, group_by: 'model', total_requests: 10,
  total_failed: 1, total_tokens: 1200, total_cost_cents: 40, daily: [],
};
const EFFICIENCY: EfficiencySummary = {
  scope: 'user', group_by: 'model',
  from: '2026-08-16T00:00:00Z', to: '2026-09-15T00:00:00Z',
  groups: [{ key: 'doubao', label: 'doubao', run_count: 10, failed_runs: 1,
             avg_run_ms: 41000, tool_calls: 40, tool_errors: 4, tool_error_rate: 0.1,
             deliverables: 8, cost_cents: 40, cost_per_deliverable_cents: 5 }],
  turn_end_reasons: { completed: 8, awaiting_input: 1, error: 1 },
};

describe('StatTiles', () => {
  it('shows cost per output and tool error rate', () => {
    render(<StatTiles summary={SUMMARY} efficiency={EFFICIENCY} />);
    expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('¢5.00');
    expect(screen.getByTestId('tile-tool-errors')).toHaveTextContent('10.0%');
  });

  it('reads success from the turn-end mix, not from run status', () => {
    // 10 个 run 里 8 个 completed → 80%。按 status 算会是 90%（只有 1 个 failed）。
    render(<StatTiles summary={SUMMARY} efficiency={EFFICIENCY} />);
    expect(screen.getByTestId('tile-success')).toHaveTextContent('80%');
  });

  it('falls back to an em dash while efficiency is absent or produced nothing', () => {
    // 效率是第二个请求；它还没回来时这两格必须显式「不知道」，不是 100%。
    const { unmount } = render(<StatTiles summary={SUMMARY} efficiency={null} />);
    expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('—');
    expect(screen.getByTestId('tile-success')).toHaveTextContent('—');
    unmount();
    const groups = [{ ...EFFICIENCY.groups[0], deliverables: 0, cost_per_deliverable_cents: null }];
    render(<StatTiles summary={SUMMARY} efficiency={{ ...EFFICIENCY, groups }} />);
    expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('—');
  });

  it('says 0.0% for a window that called tools and never erred, not an em dash', () => {
    // 「调过 40 次工具、0 次出错」是一个确定的好消息；退成「—」把它说成不知道。
    const groups = [{ ...EFFICIENCY.groups[0], tool_errors: 0, tool_error_rate: 0 }];
    render(<StatTiles summary={SUMMARY} efficiency={{ ...EFFICIENCY, groups }} />);
    expect(screen.getByTestId('tile-tool-errors')).toHaveTextContent('0.0%');
  });

  it('em-dashes the tool error rate when no tool was ever called', () => {
    // 0 次调用的错误率没有定义——0.0% 会读成「工具很稳」。
    const groups = [{ ...EFFICIENCY.groups[0], tool_calls: 0, tool_errors: 0, tool_error_rate: 0 }];
    render(<StatTiles summary={SUMMARY} efficiency={{ ...EFFICIENCY, groups }} />);
    expect(screen.getByTestId('tile-tool-errors')).toHaveTextContent('—');
  });

  it('sums cost and deliverables across groups instead of reading only the first', () => {
    // 按 model 分组时一个窗口常有好几行；只读第一行的单价对多模型用户永远是错的。
    const eff: EfficiencySummary = {
      ...EFFICIENCY,
      groups: [
        { ...EFFICIENCY.groups[0], cost_cents: 30, deliverables: 2, cost_per_deliverable_cents: 15 },
        { ...EFFICIENCY.groups[0], key: 'qwen', label: 'qwen', cost_cents: 10, deliverables: 2, cost_per_deliverable_cents: 5 },
      ],
    };
    render(<StatTiles summary={SUMMARY} efficiency={eff} />);
    expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('¢10.00');
  });

  it('keeps the four legacy tiles reading from the summary', () => {
    render(<StatTiles summary={SUMMARY} efficiency={EFFICIENCY} />);
    expect(screen.getByTestId('tile-requests')).toHaveTextContent('10');
  });

  // ─── 修复轮 1 #1：Success 的分母是「结束过的回合」，不是全部 run ──────────
  // 后端 reasons 查询带 `WHERE turn_end_reason IS NOT NULL`，而 mig 472 不回填存量
  // 行。拿 sum(run_count) 当分母，上线首月这一格会显示个位数——一个跑得好好的
  // 窗口被报成惨败。分布与分母必须同源。

  it('divides by the runs that actually ended, not by every run', () => {
    // 10 个 run，只有 3 个带 turn_end_reason（其余是 mig 472 之前的存量行）。
    // 2/3 = 67%；拿 run_count 当分母会是 2/10 = 20%。
    const eff: EfficiencySummary = {
      ...EFFICIENCY,
      turn_end_reasons: { completed: 2, error: 1 },
    };
    render(<StatTiles summary={SUMMARY} efficiency={eff} />);
    expect(screen.getByTestId('tile-success')).toHaveTextContent('67%');
  });

  it('says it does not know when no run has ended yet', () => {
    // run_count 是 10，但一个 reason 都没有 → 分母是 0。0% 会把「还没有人结束」
    // 说成「全都失败了」。
    const eff: EfficiencySummary = { ...EFFICIENCY, turn_end_reasons: {} };
    render(<StatTiles summary={SUMMARY} efficiency={eff} />);
    expect(screen.getByTestId('tile-success')).toHaveTextContent('—');
  });

  it('survives a response that carried no turn_end_reasons field at all', () => {
    // 旧后端 / 半截响应：字段整个缺席。读它的 `.completed` 会炸，落「—」才对。
    const eff = { ...EFFICIENCY } as EfficiencySummary;
    delete (eff as Partial<EfficiencySummary>).turn_end_reasons;
    render(<StatTiles summary={SUMMARY} efficiency={eff} />);
    expect(screen.getByTestId('tile-success')).toHaveTextContent('—');
  });

  it('counts an unknown reason toward the denominator, not toward success', () => {
    // runner 明天加一个终止理由，这一格不该因此虚高。
    const eff: EfficiencySummary = {
      ...EFFICIENCY,
      turn_end_reasons: { completed: 1, some_new_reason: 1 },
    };
    render(<StatTiles summary={SUMMARY} efficiency={eff} />);
    expect(screen.getByTestId('tile-success')).toHaveTextContent('50%');
  });
});
