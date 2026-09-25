import { render, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import { IssueChatThread } from './IssueChatThread';
import type { AgentRef } from './types';

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

describe('IssueChatThread — 运行组卡展开对话流深链', () => {
  const agents: Record<string, AgentRef> = { a1: { id: 'a1', slug: 'w', name: 'Writer' } };

  function renderThread(props: { teamId?: string; aiSessionId?: string | null }) {
    return render(
      <MemoryRouter>
        <IssueChatThread
          messages={[_run(), _run()] as never}
          agentsById={agents}
          selfUserId="u1"
          {...props}
        />
      </MemoryRouter>,
    );
  }

  it('links the run group to the session view when the issue has a session', () => {
    const { container } = renderThread({ teamId: '8', aiSessionId: '7300000000000000123' });
    const link = container.querySelector('[data-testid="run-group-open-conversation"]') as HTMLAnchorElement;
    expect(link).not.toBeNull();
    expect(link.getAttribute('href')).toBe('/team/8/ai-library/sessions/7300000000000000123');
  });

  it('renders no link when the issue has no session yet', () => {
    const { container } = renderThread({ teamId: '8', aiSessionId: null });
    expect(container.querySelector('[data-testid="run-group-open-conversation"]')).toBeNull();
  });

  it('renders no link when the team id is unknown', () => {
    const { container } = renderThread({ aiSessionId: '7300000000000000123' });
    expect(container.querySelector('[data-testid="run-group-open-conversation"]')).toBeNull();
  });

  it('keeps the collapse toggle working alongside the link', () => {
    const { container } = renderThread({ teamId: '8', aiSessionId: '7300000000000000123' });
    fireEvent.click(container.querySelector('[data-testid="run-group-toggle"]') as HTMLElement);
    expect(container.querySelector('[data-testid="run-group-body"]')).not.toBeNull();
  });
});

describe('IssueChatThread — 交付物卡', () => {
  it('renders a deliverable upload as an ok-tinted card naming the file', () => {
    const msg = {
      id: 'd-1',
      issue_id: 1,
      kind: 'comment',
      author_user_id: 'u1',
      author_agent_id: null,
      body: 'Filed deliverable: pilot-v3.fdx',
      created_at: '2026-05-26T12:00:00Z',
      meta: { deliverable_upload: true, file_id: 'f-1', filename: 'pilot-v3.fdx' },
    };
    const { container } = render(
      <IssueChatThread messages={[msg as never]} agentsById={{}} selfUserId="u1" />,
    );
    const row = container.querySelector('[data-testid="deliverable-filed-row"]') as HTMLElement;
    expect(row).not.toBeNull();
    expect(row.textContent).toContain('pilot-v3.fdx');
    // A deliverable is an outcome, not a status murmur — it gets a real card.
    expect(row.className).toMatch(/border-ok-line/);
    expect(row.className).toMatch(/bg-ok-soft/);
    expect(row.className).not.toMatch(/emerald/);
  });
});

/**
 * 已发出的引用卡上的来源议题 chip（harness 三期 3c §2.4）。
 *
 * Task 16 把引用归属从「必须是本议题产出」放宽成「链对你可见」，于是一条评论
 * 里的引用可能指着**别的**议题的产出。后端因此在附件行上带回 `issue_key`。
 * 前端整整没读过同族字段的先例就在 CLAUDE.md 里（`attachment_failures`），所以
 * 这两条钉的是「读了」以及「只在真的来自别处时才说」。
 */
describe('IssueChatThread — 引用卡的来源议题', () => {
  const _cite = (issueKey?: string | null) =>
    ({
      id: 'm-cite',
      issue_id: 1,
      kind: 'comment',
      author_user_id: 'u1',
      author_agent_id: null,
      body: 'tighten this',
      created_at: '2026-09-15T12:00:00Z',
      meta: {},
      attachments: [
        {
          kind: 'output_ref',
          ref_kind: 'script_shot',
          ref_id: '727145299382534999',
          version: 2,
          title: 'S3 · Shot #1',
          ...(issueKey === undefined ? {} : { issue_key: issueKey }),
        },
      ],
    }) as const;

  it('标出来自另一件议题的引用', () => {
    const { container } = render(
      <MemoryRouter>
        <IssueChatThread
          messages={[_cite('MH-98') as never]}
          agentsById={{}}
          selfUserId="u1"
          issueKey="MH-96"
        />
      </MemoryRouter>,
    );
    const mark = container.querySelector('[data-testid="output-chip-issue"]');
    expect(mark?.textContent).toBe('MH-98');
  });

  it('本议题自己的产出不画 chip —— 指着你正看着的那件议题什么也没说', () => {
    const { container } = render(
      <MemoryRouter>
        <IssueChatThread
          messages={[_cite('MH-96') as never]}
          agentsById={{}}
          selfUserId="u1"
          issueKey="MH-96"
        />
      </MemoryRouter>,
    );
    expect(container.querySelector('[data-testid="output-chip-issue"]')).toBeNull();
  });

  it('3c 之前写下的引用没有这个字段，照旧渲染', () => {
    const { container } = render(
      <MemoryRouter>
        <IssueChatThread
          messages={[_cite(undefined) as never]}
          agentsById={{}}
          selfUserId="u1"
          issueKey="MH-96"
        />
      </MemoryRouter>,
    );
    expect(container.querySelector('[data-testid="comment-citations"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="output-chip-issue"]')).toBeNull();
  });
});
