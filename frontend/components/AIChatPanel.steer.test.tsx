/**
 * T10 (harness P4 §1-③): while a turn is streaming, the composer stays open
 * and a message is a STEER — delivered to the conversation's inbox, not a
 * second turn. Idle → the ordinary send. Chat | Trajectory toggle over the
 * same messages.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key) }),
}));

const streamChatMessage = vi.fn();
let releaseStream: (() => void) | null = null;
const SESSION = { id: '3107', title: 'New conversation', agent_slug: 'analyze' };

vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: vi.fn(async () => [{ id: '1', slug: 'analyze', name: 'Analyze', enabled: true }]),
    listChatSessions: vi.fn(async () => [SESSION]),
    listAllChatSessions: vi.fn(async () => []),
    getChatSession: vi.fn(async () => ({ ...SESSION, messages: [] })),
    createChatSession: vi.fn(async () => SESSION),
    updateChatSession: vi.fn(async () => SESSION),
    deleteChatSession: vi.fn(async () => undefined),
    streamChatMessage: (...args: unknown[]) => streamChatMessage(...args),
    uploadChatAttachment: vi.fn(),
    getRunEvents: vi.fn(async () => ({ items: [], count: 0 })),
    listCommitments: vi.fn(async () => ({ items: [], count: 0 })),
  },
}));
const deliverSteer = vi.fn();
vi.mock('../services/agentInboxService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../services/agentInboxService')>();
  return { ...mod, deliverSteer: (...args: unknown[]) => deliverSteer(...args) };
});
vi.mock('../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({ data: { results: [], counts: {} }, loading: false, error: null }),
}));
vi.mock('../utils/ensureResourceProcessed', () => ({ ensureResourceProcessed: vi.fn(async () => ({ action: 'ready' })) }));
vi.mock('../hooks/useResourceProcessingFollowUps', () => ({ useResourceProcessingFollowUps: () => undefined }));
vi.mock('../hooks/useOptionalTaskManager', () => ({ useOptionalTaskManager: () => null }));

let composerProps: { onSend: (text: string, refs: unknown[]) => void; disabled?: boolean; placeholder?: string } | null = null;
vi.mock('./chat/ChatInput', () => ({
  ChatInput: (props: { onSend: (text: string, refs: unknown[]) => void; disabled?: boolean; placeholder?: string }) => {
    composerProps = props;
    return <div data-testid="composer" data-disabled={String(!!props.disabled)} data-placeholder={props.placeholder ?? ''} />;
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

beforeEach(() => {
  vi.clearAllMocks();
  composerProps = null;
  releaseStream = null;
  Element.prototype.scrollIntoView = vi.fn();
  useGlobalChatStore.setState({ pendingResource: null, pendingQuote: null, chatRequest: null });
  deliverSteer.mockResolvedValue({ id: '9', kind: 'steer' });
  // A stream that stays open until the test releases it — "the turn is running".
  streamChatMessage.mockImplementation(async function* () {
    yield { type: 'delta', data: { text: 'drafting…' } };
    await new Promise<void>((resolve) => { releaseStream = resolve; });
    yield { type: 'done', data: {} };
  });
});

describe('composer while a turn runs = steer, idle = send', () => {
  it('idle: sending goes to the stream, not the inbox', async () => {
    streamChatMessage.mockImplementation(async function* () { yield { type: 'done', data: {} }; });
    await renderPanel();
    composerProps!.onSend('hello', []);
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));
    expect(deliverSteer).not.toHaveBeenCalled();
  });

  it('running: the composer stays enabled with the steer placeholder and posts to the conversation inbox', async () => {
    await renderPanel();
    composerProps!.onSend('first', []);
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(composerProps?.placeholder).toMatch(/Steer the running agent/));
    expect(composerProps?.disabled).toBe(false);
    composerProps!.onSend('colder, please', []);
    await waitFor(() => expect(deliverSteer).toHaveBeenCalledWith('conversation', '3107', 'colder, please'));
    // no second turn was started
    expect(streamChatMessage).toHaveBeenCalledTimes(1);
    // the steer shows in the thread right away
    expect(screen.getByText('colder, please')).toBeTruthy();
    releaseStream?.();
    await waitFor(() => expect(composerProps?.placeholder).toBe('Type a message...'));
  });

  it('offers Chat | Trajectory over the same messages, and Trajectory shows one block per run', async () => {
    streamChatMessage.mockImplementation(async function* () { yield { type: 'done', data: {} }; });
    const { aiLibraryService } = await import('../services/aiLibraryService');
    (aiLibraryService.getChatSession as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...SESSION,
      messages: [
        { id: 'u1', session_id: '3107', role: 'user', content: 'hello', created_at: '2026-09-05T00:00:00Z' },
        { id: 'a1', session_id: '3107', role: 'assistant', content: 'hi', created_at: '2026-09-05T00:00:01Z', metadata_json: { run_id: '701' } },
      ],
    });
    await renderPanel();
    await waitFor(() => expect(screen.getByTestId('chat-view-toggle')).toBeTruthy());
    fireEvent.click(screen.getByText('Trajectory'));
    const block = await screen.findByTestId('chat-run-block');
    expect(block.getAttribute('data-run-id')).toBe('701');
    fireEvent.click(screen.getByText('Chat'));
    expect(screen.queryByTestId('chat-run-block')).toBeNull();
    expect(screen.getByText('hi')).toBeTruthy();
  });
});
