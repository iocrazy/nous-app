/**
 * 3c §4.1/§4.2：聊天面板这一侧的三条新接线。
 *
 * 1. SSE `start` 帧把 run id 交过来 → 运行中状态行终于画得出来。在此之前它**永不
 *    渲染**：临时气泡没有 `metadata_json`，而 `run_id` 只在 `done` 帧才回。
 * 2. `done` 帧的花费落进 `runCosts` → 刚结束的那条气泡当场有尾栏，不必等批量取数。
 * 3. 这屏一个 run 都没有时不发请求 —— 空 ids 打一次库是白打。
 *
 * 单独成文件而不是塞进 `AIChatPanel.steer.test.tsx`：状态行要 mock
 * `useRunToolActivity`，而那个 mock 会影响 steer 文件里的 Trajectory 用例。
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: unknown, opts?: unknown) => {
      const template = typeof def === 'string' ? def : key;
      const vars = (typeof def === 'object' ? def : opts) as Record<string, unknown> | undefined;
      return vars ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? '')) : template;
    },
  }),
}));

const streamChatMessage = vi.fn();
const getRunCosts = vi.fn(async () => ({}));
const SESSION = { id: '3107', title: 'New conversation', agent_slug: 'analyze' };
// 回合结束后面板会重拉历史；这里让助手消息带上 run_id，尾栏才有 run 可认。
let historyMessages: unknown[] = [];

vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: vi.fn(async () => [{ id: '1', slug: 'analyze', name: 'Analyze', enabled: true }]),
    listChatSessions: vi.fn(async () => [SESSION]),
    listAllChatSessions: vi.fn(async () => []),
    getChatSession: vi.fn(async () => ({ ...SESSION, messages: historyMessages })),
    createChatSession: vi.fn(async () => SESSION),
    updateChatSession: vi.fn(async () => SESSION),
    deleteChatSession: vi.fn(async () => undefined),
    streamChatMessage: (...args: unknown[]) => streamChatMessage(...args),
    uploadChatAttachment: vi.fn(),
    getRunEvents: vi.fn(async () => ({ items: [], count: 0 })),
    listCommitments: vi.fn(async () => ({ items: [], count: 0 })),
    getRunCosts: (...args: unknown[]) => getRunCosts(...(args as [])),
  },
}));

// 状态行读的是这个 hook 的 `nodes`。真值来自网络，这里按 run id 给一个「正在跑的
// 第 3 步」——`liveStep` 只认最后一个 `kind:'step'` 且 `live:true` 的节点。
const useRunToolActivityMock = vi.fn();
vi.mock('./agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: (...args: unknown[]) => useRunToolActivityMock(...args),
}));

vi.mock('../services/agentInboxService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../services/agentInboxService')>();
  return { ...mod, deliverSteer: vi.fn(async () => ({ id: '9', kind: 'steer' })) };
});
vi.mock('../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({ data: { results: [], counts: {} }, loading: false, error: null }),
}));
vi.mock('../utils/ensureResourceProcessed', () => ({ ensureResourceProcessed: vi.fn(async () => ({ action: 'ready' })) }));
vi.mock('../hooks/useResourceProcessingFollowUps', () => ({ useResourceProcessingFollowUps: () => undefined }));
vi.mock('../hooks/useOptionalTaskManager', () => ({ useOptionalTaskManager: () => null }));

let composerProps: { onSend: (text: string, refs: unknown[]) => void; disabled?: boolean } | null = null;
vi.mock('./chat/ChatInput', () => ({
  ChatInput: (props: { onSend: (text: string, refs: unknown[]) => void; disabled?: boolean }) => {
    composerProps = props;
    return <div data-testid="composer" data-disabled={String(!!props.disabled)} />;
  },
}));

import { AIChatPanel } from './AIChatPanel';
import { ToastProvider } from './Toast';
import { useGlobalChatStore } from '../stores/globalChatStore';

async function renderPanel() {
  const view = render(
    <ToastProvider>
      <AIChatPanel projectId="1" agentSlug="analyze" />
    </ToastProvider>,
  );
  await waitFor(() => expect(composerProps?.disabled).toBe(false));
  return view;
}

const LIVE_STEP = [{ kind: 'step', step: 3, live: true, startedAt: Date.now(), lines: [] }];

beforeEach(() => {
  vi.clearAllMocks();
  composerProps = null;
  historyMessages = [];
  Element.prototype.scrollIntoView = vi.fn();
  useGlobalChatStore.setState({ pendingResource: null, pendingQuote: null, chatRequest: null });
  getRunCosts.mockResolvedValue({});
  useRunToolActivityMock.mockReturnValue({ events: [], denials: [], activities: [], nodes: [], loaded: true });
});

describe('AIChatPanel — 回合的 run id 与花费（3c §4.1/§4.2）', () => {
  it('`start` 帧一到，运行中状态行就画得出来——此前它整场回合都没有 run 可读', async () => {
    let release: (() => void) | null = null;
    streamChatMessage.mockImplementation(async function* () {
      yield { type: 'start', data: { run_id: '701' } };
      yield { type: 'delta', data: { text: 'drafting…' } };
      await new Promise<void>((resolve) => { release = resolve; });
      yield { type: 'done', data: { run_id: '701' } };
    });
    // 只有被问到 701 时才报「正在跑」——这样断言钉的是 run id 真的传到了，
    // 而不是「状态行总会出现」。
    useRunToolActivityMock.mockImplementation((runId: string | null) => ({
      events: [], denials: [], activities: [], loaded: true,
      nodes: runId === '701' ? LIVE_STEP : [],
    }));

    await renderPanel();
    composerProps!.onSend('hello', []);

    await waitFor(() => expect(screen.getByTestId('run-status-line')).toBeTruthy());
    expect(screen.getByTestId('run-status-line').textContent).toContain('Step 3');
    release?.();
  });

  it('没有 `start` 帧就没有状态行——负向对照，证明上面那条不是白给的', async () => {
    let release: (() => void) | null = null;
    streamChatMessage.mockImplementation(async function* () {
      yield { type: 'delta', data: { text: 'drafting…' } };
      await new Promise<void>((resolve) => { release = resolve; });
      yield { type: 'done', data: {} };
    });
    useRunToolActivityMock.mockImplementation((runId: string | null) => ({
      events: [], denials: [], activities: [], loaded: true,
      nodes: runId === '701' ? LIVE_STEP : [],
    }));

    await renderPanel();
    composerProps!.onSend('hello', []);

    await waitFor(() => expect(screen.getByText('drafting…')).toBeTruthy());
    expect(screen.queryByTestId('run-status-line')).toBeNull();
    release?.();
  });

  it('`done` 帧的花费当场落进尾栏——不必等下一次批量取数', async () => {
    historyMessages = [
      { id: 'u1', session_id: '3107', role: 'user', content: 'hello', created_at: '2026-09-05T00:00:00Z' },
      {
        id: 'a1', session_id: '3107', role: 'assistant', content: 'hi',
        created_at: '2026-09-05T00:00:01Z', metadata_json: { run_id: '701' },
      },
    ];
    streamChatMessage.mockImplementation(async function* () {
      yield { type: 'start', data: { run_id: '701' } };
      yield {
        type: 'done',
        data: { run_id: '701', cost_cents: 0.82, charged_points: 0.82 },
      };
    });
    // 批量取数回空：这样尾栏上的数字只可能来自 `done` 帧。
    getRunCosts.mockResolvedValue({});

    await renderPanel();
    composerProps!.onSend('hello', []);

    await waitFor(() => expect(screen.getByTestId('run-cost-tail')).toBeTruthy());
    expect(screen.getByTestId('run-cost-tail').textContent).toBe('◇ 0.82');
  });

  it('这屏一个 run 都没有时不发请求——空 ids 打一次库是白打', async () => {
    streamChatMessage.mockImplementation(async function* () { yield { type: 'done', data: {} }; });
    await renderPanel();
    expect(getRunCosts).not.toHaveBeenCalled();
  });

  it('有 run 的历史会被问一次账，且只问去重后的那些', async () => {
    historyMessages = [
      { id: 'a1', session_id: '3107', role: 'assistant', content: 'one', created_at: '2026-09-05T00:00:01Z', metadata_json: { run_id: '701' } },
      { id: 'a2', session_id: '3107', role: 'assistant', content: 'two', created_at: '2026-09-05T00:00:02Z', metadata_json: { run_id: '701' } },
      { id: 'a3', session_id: '3107', role: 'assistant', content: 'three', created_at: '2026-09-05T00:00:03Z', metadata_json: { run_id: '702' } },
    ];
    await renderPanel();
    await waitFor(() => expect(getRunCosts).toHaveBeenCalled());
    expect(getRunCosts).toHaveBeenCalledWith(['701', '702']);
    expect(getRunCosts).toHaveBeenCalledTimes(1);
  });
});
