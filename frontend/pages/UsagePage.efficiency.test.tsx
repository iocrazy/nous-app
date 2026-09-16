/**
 * 用量页对效率端点的态度（3c §3.3）：它是**第二个**请求。
 *
 * 它挂掉不该把整页打掉——页面已经拿到的日汇总仍然是真的；两枚效率格退回「—」，
 * 并且要说出一句类型化的话。把 `ErrorResponse` 外壳原样渲出来（生产是
 * `{success,error,code:"http_503",details:{code}}`）等于把 request_id 甩给用户。
 */
import React from 'react';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, f?: string) => f ?? k }),
}));
vi.mock('react-router-dom', () => ({ useParams: () => ({}) }));
// recharts 的 ResponsiveContainer 在 jsdom 里量不到尺寸，会一直重试重排直到把
// worker 撑爆（实测 78s 后 OOM）。这个测试问的是接线，不是图表。
vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    Bar: Stub, BarChart: Stub, CartesianGrid: Stub, Legend: Stub,
    ResponsiveContainer: Stub, Tooltip: Stub, XAxis: Stub, YAxis: Stub,
  };
});
// 必须是**同一个**对象：`fetchUsage` 把 `addToast` 放进了 useCallback 的依赖，
// 每次渲染发一个新函数会让 effect 每帧重跑，实测把 worker 撑到 OOM。
const toast = { addToast: vi.fn() };
vi.mock('../components/Toast', () => ({ useToast: () => toast }));

const getUsageDaily = vi.fn();
const getUsageRuns = vi.fn();
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    getUsageDaily: (...a: unknown[]) => getUsageDaily(...a),
    getUsage: vi.fn(),
    getUsageRuns: (...a: unknown[]) => getUsageRuns(...a),
  },
}));

const getEfficiency = vi.fn();
vi.mock('../services/usageService', () => ({
  usageService: { getEfficiency: (...a: unknown[]) => getEfficiency(...a) },
  UsageRequestError: class UsageRequestError extends Error {
    code: string;
    status: number;
    constructor(code: string, status: number, message: string) {
      super(message);
      this.code = code;
      this.status = status;
    }
  },
}));

const { UsagePage } = await import('./UsagePage');
const { UsageRequestError } = await import('../services/usageService');

const SUMMARY = {
  days: 30, month: null, group_by: 'model', total_requests: 10,
  total_failed: 1, total_tokens: 1200, total_cost_cents: 40, daily: [],
};
const EFFICIENCY = {
  scope: 'user', group_by: 'model',
  from: '2026-08-16T00:00:00Z', to: '2026-09-15T00:00:00Z',
  groups: [{ key: 'doubao', label: 'doubao', run_count: 10, failed_runs: 1,
             avg_run_ms: 41000, tool_calls: 40, tool_errors: 4, tool_error_rate: 0.1,
             deliverables: 8, cost_cents: 40, cost_per_deliverable_cents: 5 }],
  turn_end_reasons: { completed: 8, awaiting_input: 1, error: 1 },
};

beforeEach(() => {
  getUsageDaily.mockReset().mockResolvedValue(SUMMARY);
  getUsageRuns.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 });
  getEfficiency.mockReset().mockResolvedValue(EFFICIENCY);
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('UsagePage efficiency wiring', () => {
  it('draws the turn-end breakdown once efficiency lands', async () => {
    render(<UsagePage />);
    await waitFor(() => expect(screen.getByTestId('turn-end-completed')).toBeInTheDocument());
    expect(screen.getByTestId('tile-success')).toHaveTextContent('80%');
  });

  it('keeps the page and names the failure when efficiency is unavailable', async () => {
    getEfficiency.mockRejectedValue(
      new UsageRequestError('efficiency_unavailable', 503, 'Service Unavailable'),
    );
    render(<UsagePage />);
    // 日汇总那四格仍在——效率挂了不等于用量读不到。
    await waitFor(() => expect(screen.getByTestId('tile-requests')).toHaveTextContent('10'));
    expect(screen.getByTestId('tile-success')).toHaveTextContent('—');
    expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('—');
    const notice = screen.getByTestId('efficiency-error');
    expect(notice).toHaveTextContent('Efficiency stats are unavailable right now');
    // 外壳字段一个都不许出现在屏幕上。
    expect(notice.textContent).not.toMatch(/http_503|request_id|success/);
  });

  it('asks for efficiency in the same grouping the charts use', async () => {
    render(<UsagePage />);
    await waitFor(() => expect(getEfficiency).toHaveBeenCalled());
    expect(getEfficiency).toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'user', groupBy: 'model' }),
    );
  });
});
