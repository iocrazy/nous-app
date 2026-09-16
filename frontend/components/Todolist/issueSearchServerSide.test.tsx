/**
 * Issues 页的搜索改服务端（3c §2.3）。三件事：防抖 250 ms 后只发一次；本地
 * `includes` 已删（服务端已筛过，再筛一次会二次裁剪服务端的命中，出现
 * 「搜到了却不显示」，而两处口径的差异不会有任何地方报错）；总数按
 * 「N of M issues」显示。
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// `t(key, fallback, vars)` —— fallback 是真实 UI 文案，所以断言读起来跟屏幕
// 一样；`{{n}}` 自己插值，否则「N of M」这条断言测不到任何东西。
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      if (typeof fallback !== 'string') return key;
      if (!vars) return fallback;
      return fallback.replace(/\{\{(\w+)\}\}/g, (_m, name: string) =>
        String(vars[name] ?? `{{${name}}}`),
      );
    },
  }),
}));
vi.mock('../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ needsInputItems: [] }),
}));
vi.mock('../../services/agentInboxService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/agentInboxService')>();
  return { ...mod, fetchPendingSummary: async () => ({}) };
});
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    listApprovalRequests: vi.fn(async () => ({ items: [], count: 0 })),
    approveRequest: vi.fn(async () => ({ id: 'x', status: 'approved' })),
    rejectRequest: vi.fn(async () => ({ id: 'x', status: 'rejected' })),
  },
}));

import { IssueListView } from './IssueListView';
import { baseProps, uiIssue } from './issueListTestFactories';

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const mount = (props: Record<string, unknown>) =>
  render(
    <MemoryRouter>
      <IssueListView {...baseProps()} {...props} />
    </MemoryRouter>,
  );

describe('Issues 页服务端搜索', () => {
  it('debounces to one call 250 ms after the last keystroke', () => {
    const onSearchChange = vi.fn();
    mount({ onSearchChange });
    const box = screen.getByPlaceholderText('Search issues…');
    for (const v of ['ra', 'rai', 'rain']) fireEvent.change(box, { target: { value: v } });
    act(() => {
      vi.advanceTimersByTime(249);
    });
    expect(onSearchChange).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onSearchChange).toHaveBeenCalledTimes(1);
    expect(onSearchChange).toHaveBeenCalledWith('rain');
  });

  it('renders every row the server returned, without a second local filter', () => {
    // 服务端按 description 命中的一行，前端 haystack 里没有那个词。本地
    // includes 还在的话这一行会被二次裁掉。
    mount({
      issues: [uiIssue({ identifier: 'MH-96', title: 'Alpha', description: null })],
      onSearchChange: vi.fn(),
      serverQuery: 'rain',
    });
    const box = screen.getByPlaceholderText('Search issues…');
    fireEvent.change(box, { target: { value: 'rain' } });
    expect(screen.getByText(/MH-96/)).toBeTruthy();
  });

  it('says N of M while searching and a plain count otherwise', () => {
    const { unmount } = mount({
      issues: [uiIssue({ identifier: 'MH-96' })],
      onSearchChange: vi.fn(),
      serverQuery: 'rain',
      totalCount: 37,
    });
    expect(screen.getByTestId('issue-list-count').textContent).toContain('1 of 37');
    unmount();
    mount({ issues: [uiIssue({ identifier: 'MH-96' })], onSearchChange: vi.fn() });
    const text = screen.getByTestId('issue-list-count').textContent ?? '';
    expect(text).toContain('1 issues');
    expect(text).not.toContain(' of ');
  });
});
