/**
 * 团队用量页 Totals 六格（3c §3.3，修复轮 1 #7）。
 *
 * 两个纯函数的规则已经在 `usagePanelHelpers.test.ts` 钉住了；这里钉的是另一件事：
 * 那两格**真的挂上去了**，而且网格从 4 列改成了 6 列。只测格式化函数的话，把两个
 * `<Stat>` 从 JSX 里删掉，测试照样全绿。
 */
import React from 'react';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, f?: string) => f ?? k }),
}));
vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    Bar: Stub, BarChart: Stub, CartesianGrid: Stub, Legend: Stub,
    ResponsiveContainer: Stub, Tooltip: Stub, XAxis: Stub, YAxis: Stub,
  };
});
// 稳定单例：每次渲染发新对象会让 `load` 的 useCallback 每帧重建，effect 打转。
const toast = { addToast: vi.fn() };
vi.mock('../components/Toast', () => ({ useToast: () => toast }));
vi.mock('../hooks/useWorkspaceScope', () => ({
  useWorkspaceScope: () => ({ scopeId: '424242424242', scopeType: 'team' }),
}));

const getSummary = vi.fn();
const getTeamBudget = vi.fn();
vi.mock('../services/usageService', () => ({
  usageService: {
    getSummary: (...a: unknown[]) => getSummary(...a),
    getTeamBudget: (...a: unknown[]) => getTeamBudget(...a),
    setTeamBudget: vi.fn(),
  },
}));
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));

const { TeamAiUsagePage } = await import('./TeamAiUsagePage');

// 真实 wire 形状（`backend/app/schemas/usage.py::UsageTotals`）：五个计数 + 一个可空单价。
const total = (over: Record<string, unknown> = {}) => ({
  prompt_tokens: 900, completion_tokens: 120, total_tokens: 1020,
  cached_input_tokens: 0, cost_cents: 40.5, event_count: 12,
  run_count: 12, failed_runs: 1, tool_calls: 40, tool_errors: 4,
  deliverables: 8, cost_per_deliverable_cents: 5.0625, ...over,
});

beforeEach(() => {
  getSummary.mockReset().mockResolvedValue({
    team_id: '424242424242', from: '2026-08-17T00:00:00Z', to: '2026-09-16T00:00:00Z',
    group_by: 'model', total: total(), groups: [], daily: [],
  });
  getTeamBudget.mockReset().mockResolvedValue({
    team_id: '424242424242', monthly_budget_cents: null, month_spend_cents: 0,
    over_budget: false, updated_by_user_id: null, updated_at: null,
  });
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('TeamAiUsagePage totals', () => {
  it('renders the two efficiency stats alongside the four legacy ones', async () => {
    render(<TeamAiUsagePage />);
    await waitFor(() => expect(screen.getByText('Cost / output')).toBeInTheDocument());
    expect(screen.getByText('¢5.06')).toBeInTheDocument();
    expect(screen.getByText('Tool errors')).toBeInTheDocument();
    expect(screen.getByText('10.0%')).toBeInTheDocument();
    // 四枚老格子还在。
    expect(screen.getByText('Total cost')).toBeInTheDocument();
    expect(screen.getByText('Completion')).toBeInTheDocument();
  });

  it('widens the totals grid to six columns so the new pair is not orphaned', async () => {
    const { container } = render(<TeamAiUsagePage />);
    await waitFor(() => expect(screen.getByText('Cost / output')).toBeInTheDocument());
    const grid = container.querySelector('.sm\\:grid-cols-6');
    expect(grid).not.toBeNull();
    expect(grid!.children).toHaveLength(6);
  });

  it('shows an em dash for a team that produced nothing, not a free price', async () => {
    getSummary.mockResolvedValue({
      team_id: '424242424242', from: '2026-08-17T00:00:00Z', to: '2026-09-16T00:00:00Z',
      group_by: 'model',
      total: total({ deliverables: 0, cost_per_deliverable_cents: null, tool_calls: 0, tool_errors: 0 }),
      groups: [], daily: [],
    });
    render(<TeamAiUsagePage />);
    await waitFor(() => expect(screen.getByText('Cost / output')).toBeInTheDocument());
    expect(screen.getAllByText('—')).toHaveLength(2);
  });
});
