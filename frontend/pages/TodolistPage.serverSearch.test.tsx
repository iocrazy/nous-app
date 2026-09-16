/**
 * TodolistPage 侧的服务端搜索接线（3c §2.3 修复轮 1）。三件事，每件都是
 * 「改前不会报错、只会安静地给错答案」的那一类：
 *
 *  1. 两次 `listIssues` 乱序 resolve 时，先发后到的旧响应会盖掉新词的结果；
 *  2. 搜索态下 realtime INSERT / 新建议题把行**前插**进列表——本地二次筛选
 *     已经删掉了，所以这一行绕过了服务端的 `q`，出现在一个它不匹配的结果集里；
 *  3. 拉取失败后 `issuesTotal` 还停在上一次的数，「N of M」的 M 是过期的。
 *
 * IssueListView 在这里换成一个桩：本文件测的是页面的取数与插入策略，列表的
 * 渲染由 `components/Todolist/issueSearchServerSide.test.tsx` 负责。
 */
import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Issue, IssueListResponse } from '../services/issuesService';

const listIssues = vi.fn<(f?: Record<string, unknown>) => Promise<IssueListResponse>>();
const createIssue = vi.fn();

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: unknown) => (typeof fallback === 'string' ? fallback : key) }),
}));
vi.mock('../services/issuesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../services/issuesService')>();
  return {
    ...mod,
    listIssues: (f?: Record<string, unknown>) => listIssues(f),
    createIssue: (p: unknown) => createIssue(p),
    getIssue: vi.fn(),
    getIssueByIdentifier: vi.fn(),
  };
});
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: vi.fn(async () => []) },
}));
vi.mock('../services/projectsService', () => ({
  fetchProjects: vi.fn(async () => []),
  createProject: vi.fn(),
}));
vi.mock('../services/teamService', () => ({
  fetchMyTeams: vi.fn(async () => []),
  fetchPersonalTeam: vi.fn(async () => null),
}));
// 这三个 hook 的返回值必须是**同一个对象**：页面的挂载 effect 依赖 `addToast`
// 的身份，每次渲染换一个新函数就是一个无限取数循环（第一版这么写，测试直接
// 5 秒超时而不是断言失败）。
vi.mock('../components/Toast', () => {
  const addToast = vi.fn();
  return { useToast: () => ({ addToast }) };
});
vi.mock('../contexts/AuthContext', () => {
  const auth = { currentUserId: 'u1' };
  return { useAuth: () => auth };
});
vi.mock('../hooks/useWorkspaceScope', () => {
  const scope = { scopeId: '7', isPersonal: false, effectiveTeamId: '7' };
  return { useWorkspaceScope: () => scope };
});
vi.mock('../components/Todolist/PipelinesManagerModal', () => ({ PipelinesManagerModal: () => null }));
vi.mock('../components/Todolist/NewIssueDialog', () => ({
  NewIssueDialog: (p: { onSubmit: (payload: unknown) => Promise<void> }) => (
    <button type="button" data-testid="submit-new" onClick={() => void p.onSubmit({ title: 'x' })}>submit</button>
  ),
}));
vi.mock('../components/Todolist/IssueDetailView', () => ({ IssueDetailView: () => null }));
vi.mock('../components/layout/PageHeader', () => ({ PageHeader: () => null }));

/** Captures the realtime handler the page registers, so a test can fire one. */
let realtimeHandler: ((payload: Record<string, unknown>) => void) | null = null;
vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => ({
    channel: () => ({
      on: (_evt: string, _cfg: unknown, cb: (p: Record<string, unknown>) => void) => {
        realtimeHandler = cb;
        return { subscribe: () => ({}) };
      },
    }),
    removeChannel: vi.fn(),
  }),
}));

/** Stub list surface: shows the rows + count, and drives onSearchChange. */
vi.mock('../components/Todolist/IssueListView', () => ({
  IssueListView: (p: {
    issues: { identifier: string }[];
    totalCount?: number | null;
    onSearchChange?: (q: string) => void;
    onNewIssue: () => void;
  }) => (
    <div>
      <div data-testid="rows">{p.issues.map((i) => i.identifier).join(',')}</div>
      <div data-testid="total">{p.totalCount === null || p.totalCount === undefined ? 'none' : String(p.totalCount)}</div>
      <button type="button" data-testid="search-rain" onClick={() => p.onSearchChange?.('rain')}>rain</button>
      <button type="button" data-testid="search-snow" onClick={() => p.onSearchChange?.('snow')}>snow</button>
      <button type="button" data-testid="open-new" onClick={() => p.onNewIssue()}>new</button>
    </div>
  ),
}));

import { TodolistPage } from './TodolistPage';

const row = (id: number, identifier: string): Issue =>
  ({
    id, identifier, title: identifier, status: 'backlog', priority: 'medium',
    project_id: null, team_id: '7', assignee_user_id: null, assignee_agent_id: null,
    created_by_user_id: null, created_by_agent_id: null, parent_id: null,
    created_at: '2026-09-15T00:00:00Z', updated_at: '2026-09-15T00:00:00Z',
  }) as unknown as Issue;

const page = (items: Issue[], total: number): IssueListResponse =>
  ({ items, total, limit: 200, offset: 0 });

/** A promise plus the handle to settle it later — for ordering two requests. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

const mount = () =>
  render(<MemoryRouter initialEntries={['/team/7/todolist']}><TodolistPage /></MemoryRouter>);

const rows = () => screen.getByTestId('rows').textContent ?? '';
const total = () => screen.getByTestId('total').textContent ?? '';
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

beforeEach(() => {
  realtimeHandler = null;
  listIssues.mockReset();
  createIssue.mockReset();
});
afterEach(cleanup);

describe('TodolistPage 服务端搜索接线', () => {
  it('drops a stale in-flight response instead of letting it overwrite the newer query', async () => {
    // 同一个用户先搜 rain 再改成 snow。网络把 rain 的响应拖到 snow 之后才到；
    // 没有序号守卫的话，最后落地的是 rain 的行和 rain 的 total，而搜索框里
    // 写着 snow——两者的分歧不会有任何地方报错。
    const rain = deferred<IssueListResponse>();
    const snow = deferred<IssueListResponse>();
    listIssues
      .mockResolvedValueOnce(page([], 0))          // mount
      .mockReturnValueOnce(rain.promise)           // q=rain
      .mockReturnValueOnce(snow.promise);          // q=snow
    mount();
    await flush();

    act(() => { screen.getByTestId('search-rain').click(); });
    await flush();
    act(() => { screen.getByTestId('search-snow').click(); });
    await flush();

    await act(async () => { snow.resolve(page([row(2, 'MH-SNOW')], 3)); await Promise.resolve(); });
    await act(async () => { rain.resolve(page([row(1, 'MH-RAIN')], 99)); await Promise.resolve(); });
    await flush();

    expect(rows()).toBe('MH-SNOW');
    expect(total()).toBe('3');
  });

  it('re-fetches instead of prepending a realtime INSERT while a search is active', async () => {
    // 前插的行没有经过服务端的 q。本地二次筛选已经删掉，所以它会直接坐在
    // 一个它并不匹配的结果集里。
    listIssues
      .mockResolvedValueOnce(page([], 0))
      .mockResolvedValueOnce(page([row(1, 'MH-RAIN')], 1))
      .mockResolvedValueOnce(page([row(1, 'MH-RAIN')], 1));
    mount();
    await flush();
    act(() => { screen.getByTestId('search-rain').click(); });
    await flush();
    expect(rows()).toBe('MH-RAIN');

    const before = listIssues.mock.calls.length;
    await act(async () => {
      realtimeHandler?.({ eventType: 'INSERT', new: row(9, 'MH-OTHER') });
      await Promise.resolve();
    });
    await flush();

    expect(rows()).toBe('MH-RAIN');
    expect(listIssues.mock.calls.length).toBe(before + 1);
  });

  it('still prepends a realtime INSERT when nothing is being searched', async () => {
    // 反向对照：没有搜索时前插是对的，别把它一起改没了。
    listIssues.mockResolvedValue(page([row(1, 'MH-A')], 1));
    mount();
    await flush();

    const before = listIssues.mock.calls.length;
    await act(async () => {
      realtimeHandler?.({ eventType: 'INSERT', new: row(9, 'MH-NEW') });
      await Promise.resolve();
    });
    await flush();

    expect(rows()).toBe('MH-NEW,MH-A');
    expect(listIssues.mock.calls.length).toBe(before);
  });

  it('re-fetches instead of prepending a freshly created issue while a search is active', async () => {
    // 新建走的是另一条前插路径，和 realtime 那条一样绕过服务端的 q。
    listIssues
      .mockResolvedValueOnce(page([], 0))
      .mockResolvedValueOnce(page([row(1, 'MH-RAIN')], 1))
      .mockResolvedValueOnce(page([row(1, 'MH-RAIN')], 1));
    createIssue.mockResolvedValue(row(9, 'MH-NEW'));
    mount();
    await flush();
    act(() => { screen.getByTestId('search-rain').click(); });
    await flush();

    act(() => { screen.getByTestId('open-new').click(); });
    const before = listIssues.mock.calls.length;
    await act(async () => { screen.getByTestId('submit-new').click(); await Promise.resolve(); });
    await flush();

    expect(rows()).toBe('MH-RAIN');
    expect(listIssues.mock.calls.length).toBe(before + 1);
  });

  it('still prepends a freshly created issue when nothing is being searched', async () => {
    listIssues.mockResolvedValue(page([row(1, 'MH-A')], 1));
    createIssue.mockResolvedValue(row(9, 'MH-NEW'));
    mount();
    await flush();

    act(() => { screen.getByTestId('open-new').click(); });
    const before = listIssues.mock.calls.length;
    await act(async () => { screen.getByTestId('submit-new').click(); await Promise.resolve(); });
    await flush();

    expect(rows()).toBe('MH-NEW,MH-A');
    expect(listIssues.mock.calls.length).toBe(before);
  });

  it('clears the total when a refresh fails, rather than leaving a stale M', async () => {
    listIssues
      .mockResolvedValueOnce(page([row(1, 'MH-A')], 42))
      .mockRejectedValueOnce(new Error('boom'));
    mount();
    await flush();
    expect(total()).toBe('42');

    act(() => { screen.getByTestId('search-rain').click(); });
    await flush();

    expect(total()).toBe('none');
  });
});
