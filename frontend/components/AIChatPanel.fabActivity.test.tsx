/**
 * The floating mascot mirrors the chat panel: streaming → running, an
 * unanswered question on the newest assistant turn → waiting. Issue-context
 * sessions answer on the issue thread, so the panel never shows the answer
 * card there — the mascot must not ask for an answer either.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key),
  }),
}));

const streamChatMessage = vi.fn();
let session: Record<string, unknown> = {};
let historyMessages: unknown[] = [];

vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: vi.fn(async () => [{ id: '1', slug: 'analyze', name: 'Analyze', enabled: true }]),
    listChatSessions: vi.fn(async () => [session]),
    listAllChatSessions: vi.fn(async () => []),
    getChatSession: vi.fn(async () => ({ ...session, messages: historyMessages })),
    createChatSession: vi.fn(async () => session),
    updateChatSession: vi.fn(async () => session),
    deleteChatSession: vi.fn(async () => undefined),
    streamChatMessage: (...args: unknown[]) => streamChatMessage(...args),
    uploadChatAttachment: vi.fn(),
    getRunEvents: vi.fn(async () => ({ items: [], count: 0 })),
    listCommitments: vi.fn(async () => ({ items: [], count: 0 })),
    getRunCosts: vi.fn(async () => ({})),
  },
}));
vi.mock('./agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: () => ({ events: [], denials: [], activities: [], nodes: [], loaded: true }),
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
    return <div data-testid="composer" />;
  },
}));

import { AIChatPanel } from './AIChatPanel';
import { ToastProvider } from './Toast';
import { useGlobalChatStore } from '../stores/globalChatStore';

// Wire shape: session ids are strings, `awaiting_input` carries `question_id`.
const QUESTION_TURN = {
  id: 'a1',
  session_id: '3107',
  role: 'assistant',
  content: 'Which cut?',
  created_at: '2026-09-26T00:00:01Z',
  metadata_json: {
    run_id: '701',
    awaiting_input: { question_id: 'q1', kind: 'user', prompt: 'Which cut?', options: [{ label: 'A' }], allow_free_text: true },
  },
};

async function renderPanel() {
  const view = render(
    <ToastProvider>
      <AIChatPanel projectId="1" agentSlug="analyze" />
    </ToastProvider>,
  );
  await waitFor(() => expect(composerProps?.disabled).toBe(false));
  return view;
}

const activity = () => useGlobalChatStore.getState().fabActivity;

beforeEach(() => {
  vi.clearAllMocks();
  composerProps = null;
  historyMessages = [];
  session = { id: '3107', title: 'New conversation', agent_slug: 'analyze' };
  Element.prototype.scrollIntoView = vi.fn();
  useGlobalChatStore.setState({ fabActivity: 'idle', pendingResource: null, pendingQuote: null, chatRequest: null });
});

describe('AIChatPanel → mascot activity', () => {
  it('publishes running while a turn streams and drops to idle on unmount', async () => {
    let release: (() => void) | null = null;
    streamChatMessage.mockImplementation(async function* () {
      yield { type: 'delta', data: { text: 'drafting…' } };
      await new Promise<void>((resolve) => { release = resolve; });
      yield { type: 'done', data: {} };
    });
    const view = await renderPanel();
    composerProps!.onSend('hello', []);
    await waitFor(() => expect(activity()).toBe('running'));
    view.unmount();
    expect(activity()).toBe('idle');
    release?.();
  });

  it('publishes waiting when the newest assistant turn parked on an unanswered question', async () => {
    historyMessages = [QUESTION_TURN];
    await renderPanel();
    await waitFor(() => expect(activity()).toBe('waiting'));
  });

  it('stays idle once the question is answered', async () => {
    historyMessages = [
      { ...QUESTION_TURN, metadata_json: { awaiting_input: { ...QUESTION_TURN.metadata_json.awaiting_input, answered: { value: 'A' } } } },
    ];
    await renderPanel();
    await waitFor(() => expect(composerProps).not.toBeNull());
    expect(activity()).toBe('idle');
  });

  it('never publishes waiting in an issue-context session (the answer card is not shown there)', async () => {
    session = { ...session, context_type: 'issue' };
    historyMessages = [QUESTION_TURN];
    const view = await renderPanel();
    // Give the history load a chance to land before asserting the negative.
    await waitFor(() => expect(view.getByText('Which cut?')).toBeTruthy());
    expect(activity()).toBe('idle');
    view.unmount();
    expect(activity()).toBe('idle');
  });
});
