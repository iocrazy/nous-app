/**
 * Issues 页的搜索改服务端（3c §2.3）。四件事：防抖 250 ms 后只发一次；本地
 * `includes` 已删（服务端已筛过，再筛一次会二次裁剪服务端的命中，出现
 * 「搜到了却不显示」，而两处口径的差异不会有任何地方报错）；重挂载时从
 * `serverQuery` 复原搜索词（否则点开议题进分屏会把搜索安静地清掉）；总数按
 * 「N of M issues」显示。
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// `t` 解析**真的 en.json**（含 i18next 的 _one / _other 复数选择与 {{var}}
// 插值），所以断言读起来跟屏幕一样，而且 key 拼错、复数形式缺一个，测试会红。
// 组件里另有 `t(key, 'Fallback')` 形式的调用，走字符串分支原样返回。
vi.mock('react-i18next', async () => {
  const en = (await import('../../public/locales/en.json')).default as Record<string, unknown>;
  const resolve = (key: string, opts?: Record<string, unknown>): string => {
    const parts = key.split('.');
    const leaf = parts.pop() as string;
    const parent = parts.reduce<Record<string, unknown> | undefined>(
      (o, k) => (o == null ? undefined : (o[k] as Record<string, unknown>)),
      en,
    );
    const count = opts?.count;
    const names = typeof count === 'number'
      ? [`${leaf}_${count === 1 ? 'one' : 'other'}`, leaf]
      : [leaf];
    for (const name of names) {
      const v = parent?.[name];
      if (typeof v !== 'string') continue;
      if (!opts) return v;
      return v.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(opts[n] ?? `{{${n}}}`));
    }
    return key;
  };
  return {
    useTranslation: () => ({
      t: (key: string, arg2?: unknown) =>
        typeof arg2 === 'string' ? arg2 : resolve(key, arg2 as Record<string, unknown>),
    }),
  };
});
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

const box = () => screen.getByPlaceholderText('Search issues…') as HTMLInputElement;
const count = () => screen.getByTestId('issue-list-count').textContent ?? '';

describe('Issues 页服务端搜索', () => {
  it('debounces to one call 250 ms after the last keystroke', () => {
    const onSearchChange = vi.fn();
    mount({ onSearchChange });
    for (const v of ['ra', 'rai', 'rain']) fireEvent.change(box(), { target: { value: v } });
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
    fireEvent.change(box(), { target: { value: 'rain' } });
    expect(screen.getByText(/MH-96/)).toBeTruthy();
  });

  it('restores the box from serverQuery on remount instead of clearing the search', () => {
    // 点开一条议题进分屏会重挂载这个组件。搜索词只活在组件内部 state 的话，
    // 重挂载后它是空串，250 ms 后防抖 effect 就以 '' 调 onSearchChange，把
    // 服务端结果整页重拉——用户没做任何事，搜索自己没了。
    const onSearchChange = vi.fn();
    mount({ issues: [uiIssue({ identifier: 'MH-96' })], onSearchChange, serverQuery: 'rain' });
    expect(box().value).toBe('rain');
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(onSearchChange).not.toHaveBeenCalledWith('');
  });

  it('caps the box at the 200 chars the server accepts', () => {
    // 后端是 Query(max_length=200)。放长了会 422，而前端把整个 ErrorResponse
    // 渲进列表错误区——在输入处截断，用户根本走不到那一步。
    mount({ onSearchChange: vi.fn() });
    expect(box().maxLength).toBe(200);
  });

  it('says N of M while searching and a plain count otherwise', () => {
    const { unmount } = mount({
      issues: [uiIssue({ identifier: 'MH-96' })],
      onSearchChange: vi.fn(),
      serverQuery: 'rain',
      totalCount: 37,
    });
    expect(count()).toContain('1 of 37 issues');
    unmount();
    mount({
      issues: [uiIssue({ id: 1, identifier: 'MH-1' }), uiIssue({ id: 2, identifier: 'MH-2' })],
      onSearchChange: vi.fn(),
    });
    expect(count()).toContain('2 issues');
    expect(count()).not.toContain(' of ');
  });

  it('says "1 issue", not "1 issues"', () => {
    // `{{n}} issues` 这种绕开复数的写法在最常见的那一档上就是错的。
    mount({ issues: [uiIssue({ identifier: 'MH-96' })], onSearchChange: vi.fn() });
    expect(count()).toContain('1 issue');
    expect(count()).not.toContain('1 issues');
  });

  it('counts the page the server returned, not what a phase chip narrowed it to', () => {
    // N 的口径是「服务端给了这一页多少」。快捷相位 chip 是纯显示过滤，按它
    // 缩小 N 会让计数跟着一个服务端根本不知道的条件走，M 却还是服务端的数。
    mount({
      issues: [
        uiIssue({ id: 1, identifier: 'MH-1', status: 'blocked' }),
        uiIssue({ id: 2, identifier: 'MH-2', status: 'backlog' }),
      ],
      onSearchChange: vi.fn(),
      serverQuery: 'rain',
      totalCount: 37,
    });
    expect(count()).toContain('2 of 37 issues');
    fireEvent.click(screen.getByTestId('quick-phase-blocked'));
    expect(screen.queryByText(/MH-2/)).toBeNull(); // chip 确实筛掉了一行
    expect(count()).toContain('2 of 37 issues');
  });
});
