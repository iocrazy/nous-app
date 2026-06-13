import { render } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { IssueChatThread } from './IssueChatThread';
import type { AgentRef } from './types';

// Silence the simulateAgentRunComplete import — it only needs the service shape.
vi.mock('../../services/issueMessageService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/issueMessageService')>();
  return {
    ...actual,
    simulateAgentRunComplete: vi.fn(),
  };
});

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

function _comment(opts: { author_user_id: string | null; content: string; id?: string }) {
  return {
    id: opts.id ?? 'm-1',
    issue_id: 1,
    kind: 'comment',
    author_user_id: opts.author_user_id,
    author_agent_id: null,
    content: null,
    body: opts.content,
    created_at: '2026-05-26T12:00:00Z',
    meta: {},
  } as const;
}

const _agents: Record<string, AgentRef> = {};

describe('IssueChatThread — SystemStatusEvent rendering', () => {
  it('renders SystemStatusEvent as centered italic gray row', () => {
    const msg = {
      id: 'm-sys',
      issue_id: 1,
      kind: 'system_status',
      author_user_id: 'u1',
      from_status: 'todo',
      to_status: 'in_progress',
      meta: { from_status: 'todo', to_status: 'in_progress' },
      created_at: '2026-05-26T12:00:00Z',
    };
    const { container } = render(
      <IssueChatThread messages={[msg as never]} agentsById={{}} selfUserId="u1" />,
    );
    const row = container.querySelector('[data-testid="system-status-row"]') as HTMLElement;
    expect(row).not.toBeNull();
    expect(row.textContent).toMatch(/STATUS/);
    expect(row.textContent).toMatch(/todo/);
    expect(row.textContent).toMatch(/in.progress/);
    expect(row.className).toMatch(/italic/);
    expect(row.className).toMatch(/text-center/);
  });
});

describe('IssueChatThread — CommentEvent alignment', () => {
  it('right-aligns self comment with blue-tinted bubble', () => {
    const msg = _comment({ author_user_id: 'u1', content: 'hello' });
    const { container } = render(
      <IssueChatThread
        messages={[msg as never]}
        agentsById={_agents}
        selfUserId="u1"
      />,
    );
    const wrapper = container.querySelector('[data-testid="comment-row"]') as HTMLElement;
    expect(wrapper).not.toBeNull();
    expect(wrapper.className).toMatch(/justify-end/);
    const bubble = wrapper.querySelector('[data-testid="comment-bubble"]') as HTMLElement;
    expect(bubble.className).toMatch(/bg-blue-600/);
  });

  it('left-aligns other-user comment with zinc bubble', () => {
    const msg = _comment({ author_user_id: 'someone-else', content: 'hi' });
    const { container } = render(
      <IssueChatThread
        messages={[msg as never]}
        agentsById={_agents}
        selfUserId="u1"
      />,
    );
    const wrapper = container.querySelector('[data-testid="comment-row"]') as HTMLElement;
    expect(wrapper).not.toBeNull();
    expect(wrapper.className).toMatch(/justify-start/);
    const bubble = wrapper.querySelector('[data-testid="comment-bubble"]') as HTMLElement;
    expect(bubble.className).toMatch(/bg-ink-800/);
  });
});
