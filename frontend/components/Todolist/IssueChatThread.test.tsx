import { render, fireEvent } from '@testing-library/react';
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

function _run(over: Record<string, unknown> = {}) {
  return {
    id: `run-${Math.random().toString(36).slice(2)}`,
    issue_id: 1,
    kind: 'agent_run',
    author_agent_id: 'a1',
    author_user_id: null,
    body: 'turn output',
    duration_seconds: 60,
    created_at: '2026-05-26T12:00:00Z',
    meta: { status: 'completed', model: 'qwen-max', prompt_tokens: 4000, completion_tokens: 1000, cost_cents: 12 },
    ...over,
  };
}

describe('IssueChatThread — 运行组折叠', () => {
  const agents: Record<string, AgentRef> = { a1: { id: 'a1', slug: 'w', name: 'Writer' } };

  it('folds consecutive runs into a collapsed card with the summed header', () => {
    const { container } = render(
      <IssueChatThread messages={[_run(), _run(), _run()] as never} agentsById={agents} selfUserId="u1" />,
    );
    const card = container.querySelector('[data-testid="run-group-card"]') as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.textContent).toMatch(/3 turns/);
    // 3 × 5k tokens, 3 × 12 cents.
    expect(card.textContent).toMatch(/15\.0k tok/);
    expect(card.textContent).toMatch(/\$0\.36/);
    // Collapsed: the individual turns are not rendered yet.
    expect(container.querySelector('[data-testid="run-group-body"]')).toBeNull();
  });

  it('reveals the per-turn rows when expanded', () => {
    const { container } = render(
      <IssueChatThread messages={[_run(), _run()] as never} agentsById={agents} selfUserId="u1" />,
    );
    const toggle = container.querySelector('[data-testid="run-group-toggle"]') as HTMLElement;
    fireEvent.click(toggle);
    const body = container.querySelector('[data-testid="run-group-body"]') as HTMLElement;
    expect(body).not.toBeNull();
    expect(body.querySelectorAll('[data-testid="agent-run-meta"]')).toHaveLength(2);
  });

  it('renders a lone run inline with its model / token / cost line', () => {
    const { container } = render(
      <IssueChatThread messages={[_run()] as never} agentsById={agents} selfUserId="u1" />,
    );
    expect(container.querySelector('[data-testid="run-group-card"]')).toBeNull();
    const meta = container.querySelector('[data-testid="agent-run-meta"]') as HTMLElement;
    expect(meta).not.toBeNull();
    expect(meta.textContent).toMatch(/qwen-max/);
    expect(meta.textContent).toMatch(/5\.0k tok/);
    expect(meta.textContent).toMatch(/\$0\.12/);
  });
});
