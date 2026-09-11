/**
 * TodolistPage — 详情子树不得因为 agent / project 两张 map 晚到而被掀掉。
 *
 * 真实节奏：单议题抓取只打一个端点，几乎总是先于挂载 effect 里的
 * `Promise.all([listAgents(), fetchProjects()])` 回来。所以详情已经挂好、
 * 用户已经开始打字之后，两张 map 才落地。若单议题 effect 把它们列进依赖，
 * 它会重跑、`setSelectedLoading(true)` 同步置位、渲染退回 Loading 分支，
 * 整棵 `IssueDetailView` 连同用户输入一起被 unmount。
 *
 * 断言用 **DOM 节点身份**（`toBe`）而不是文本 —— 重挂载后文本会重新渲染成
 * 一模一样的样子，骗得过任何文本断言。
 */

import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Issue } from '../services/issuesService';
import type { AILibraryAgent } from '../types';

const h = vi.hoisted(() => ({
  // Stable across renders on purpose: TodolistPage's mount effect lists
  // `addToast` in its deps, so a fresh vi.fn() per render would re-run the
  // whole load on every render and spin the page forever.
  addToast: vi.fn(),
  getIssueByIdentifier: vi.fn(),
  getIssue: vi.fn(),
  listIssues: vi.fn(),
  listAgents: vi.fn(),
  fetchProjects: vi.fn(),
  getIssueProgress: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown) => (typeof arg2 === 'string' ? arg2 : key),
  }),
}));

vi.mock('../hooks/useWorkspaceScope', () => ({
  useWorkspaceScope: () => ({ scopeId: '9', isPersonal: false, effectiveTeamId: '9' }),
}));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ currentUserId: 'u1' }) }));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: h.addToast }) }));
vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => null,
  getSupabaseAccessToken: async () => 'test-token',
}));
vi.mock('../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ needsInputItems: [] }),
}));
vi.mock('../services/teamService', () => ({
  fetchMyTeams: vi.fn(async () => []),
  fetchPersonalTeam: vi.fn(async () => null),
}));

vi.mock('../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => h.fetchProjects(...a),
  createProject: vi.fn(),
}));

vi.mock('../services/issuesService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/issuesService')>()),
  listIssues: (...a: unknown[]) => h.listIssues(...a),
  getIssue: (...a: unknown[]) => h.getIssue(...a),
  getIssueByIdentifier: (...a: unknown[]) => h.getIssueByIdentifier(...a),
  getIssueProgress: (...a: unknown[]) => h.getIssueProgress(...a),
  createIssue: vi.fn(),
  updateIssue: vi.fn(async () => ({})),
  getDispatchPreview: vi.fn(async () => null),
  dispatchIssue: vi.fn(async () => ({})),
}));

// Spread the real module: IssueChatThread calls helpers this file never stubs.
vi.mock('../services/issueMessageService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/issueMessageService')>()),
  listIssueMessages: vi.fn(async () => ({ messages: [] })),
  postIssueMessage: vi.fn(async () => ({})),
  getCommentTriggerPreview: vi.fn(async () => null),
}));

vi.mock('../services/issueChatSocket', () => ({
  openIssueChatSocket: vi.fn(() => Promise.reject(new Error('no ws in tests'))),
}));

vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: (...a: unknown[]) => h.listAgents(...a),
    listApprovalRequests: vi.fn(async () => ({ items: [] })),
    cancelRun: vi.fn(async () => undefined),
    getRunViewAt: vi.fn(async () => null),
    getRunEvents: vi.fn(async () => ({ items: [], count: 0, has_more: false })),
    getRunForks: vi.fn(async () => ({ items: [] })),
    forkRun: vi.fn(),
  },
  RunForkRejectedError: class extends Error {},
}));

vi.mock('../services/usageService', () => ({
  usageService: { getIssueUsage: vi.fn(async () => null) },
}));
vi.mock('../services/workflowService', () => ({
  fetchStageBoard: vi.fn(async () => ({ node: { brief: '', status: 'todo' } })),
}));
vi.mock('../services/outputsService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/outputsService')>()),
  listIssueOutputs: vi.fn(async () => []),
}));

// Child panels do their own fetching — out of scope for this page's test.
vi.mock('../components/Todolist/blocks/PipelineRunStrip', () => ({ PipelineRunStrip: () => null }));
vi.mock('../components/Todolist/DeliverablesZone', () => ({ DeliverablesZone: () => null }));
vi.mock('../components/Todolist/RunPipelineMenu', () => ({ RunPipelineMenu: () => null }));
vi.mock('../components/Todolist/IssueRelatedTab', () => ({ IssueRelatedTab: () => null }));

import { TodolistPage } from './TodolistPage';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

/**
 * Real wire shape: `/issues/by-identifier/{id}` returns the ORM row as-is, so
 * every BIGINT id is a JSON **number** (`ai_session_id` is the documented
 * exception — str-serialized because it only ever lands in a URL).
 */
function mkIssue(over: Partial<Issue> = {}): Issue {
  return {
    id: 347786145852700,
    issue_number: 94,
    identifier: 'MH-94',
    title: 'Draft the cold open',
    description: null,
    status: 'todo',
    priority: 'medium',
    team_id: 9,
    project_id: 7,
    parent_id: null,
    assignee_user_id: null,
    assignee_agent_id: 'a1',
    origin_kind: 'manual',
    origin_id: null,
    origin_fingerprint: 'fp-94',
    billing_code: null,
    created_by_user_id: 'u1',
    created_by_agent_id: null,
    dbos_workflow_id: null,
    ai_session_id: '347786145852701',
    execution_locked_at: null,
    execution_state: null,
    paused_at: null,
    budget_cents: null,
    request_depth: 0,
    started_at: null,
    completed_at: null,
    cancelled_at: null,
    hidden_at: null,
    created_at: '2026-09-10T01:00:00Z',
    updated_at: '2026-09-10T01:00:00Z',
    ...over,
  } as Issue;
}

const AGENT: AILibraryAgent = {
  id: 'a1',
  slug: 'script-writer',
  name: 'Script Writer',
  model: 'qwen-max',
  temperature: 0.7,
  max_tokens: 4096,
  is_system_preset: true,
  enabled: true,
  skill_ids: [],
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
};

const PROJECT = { id: 7, name: 'Pilot Season' };

let navigateTo: (path: string) => void = () => {};

const NavProbe: React.FC = () => {
  const navigate = useNavigate();
  navigateTo = navigate;
  return null;
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/team/9/todolist/MH-94']}>
      <NavProbe />
      <Routes>
        <Route path="/team/:teamId/todolist/:identifier" element={<TodolistPage />} />
        <Route path="/team/:teamId/todolist" element={<TodolistPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('TodolistPage — agent / project map 晚到不得重挂载详情子树', () => {
  let agentsDeferred: ReturnType<typeof deferred<AILibraryAgent[]>>;
  let projectsDeferred: ReturnType<typeof deferred<Array<typeof PROJECT>>>;

  beforeEach(() => {
    vi.clearAllMocks();
    agentsDeferred = deferred<AILibraryAgent[]>();
    projectsDeferred = deferred<Array<typeof PROJECT>>();
    // The single-issue endpoint wins the race — one request against two.
    h.getIssueByIdentifier.mockImplementation(async (identifier: string) =>
      mkIssue({ identifier, issue_number: identifier === 'MH-95' ? 95 : 94 }),
    );
    h.getIssue.mockImplementation(async () => mkIssue());
    h.listIssues.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
    h.getIssueProgress.mockResolvedValue(null);
    // …the two maps land about a second later, under the test's control.
    h.listAgents.mockReturnValue(agentsDeferred.promise);
    h.fetchProjects.mockReturnValue(projectsDeferred.promise);
  });

  it('keeps the detail subtree mounted when the maps resolve after the issue', async () => {
    renderPage();

    const first = await screen.findByTestId('issue-context-rail');
    // A marker the DOM node carries but a re-render never recreates: if the
    // subtree is torn down and rebuilt, the marker goes with it.
    first.setAttribute('data-probe', 'typed-comment');
    expect(h.getIssueByIdentifier).toHaveBeenCalledTimes(1);

    await act(async () => {
      agentsDeferred.resolve([AGENT]);
      projectsDeferred.resolve([PROJECT]);
      await Promise.resolve();
    });
    // The maps really did land: the mount effect got past them to the list.
    await waitFor(() => expect(h.listIssues).toHaveBeenCalled());

    expect(screen.getByTestId('issue-context-rail')).toBe(first);
    expect(screen.getByTestId('issue-context-rail').getAttribute('data-probe')).toBe('typed-comment');
    // The cause, not just the symptom: a late map must not re-run the fetch.
    expect(h.getIssueByIdentifier).toHaveBeenCalledTimes(1);
  });

  it('still refetches and remounts when the identifier itself changes', async () => {
    renderPage();

    const first = await screen.findByTestId('issue-context-rail');
    await act(async () => {
      agentsDeferred.resolve([AGENT]);
      projectsDeferred.resolve([PROJECT]);
      await Promise.resolve();
    });
    await waitFor(() => expect(h.listIssues).toHaveBeenCalled());

    // Count rather than a fixed number: before the fix the late maps have
    // already spent a second call, and this control must read the same either
    // way — what it pins is that a NEW identifier still refetches.
    const before = h.getIssueByIdentifier.mock.calls.length;
    await act(async () => { navigateTo('/team/9/todolist/MH-95'); });

    await waitFor(() => expect(h.getIssueByIdentifier.mock.calls.length).toBeGreaterThan(before));
    expect(h.getIssueByIdentifier).toHaveBeenLastCalledWith('MH-95');
    const second = await screen.findByTestId('issue-context-rail');
    expect(second).not.toBe(first);
  });
});
