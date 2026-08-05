/**
 * Surface-level half of the exactly-once guarantee.
 *
 * toolActivity.test.ts pins the normalisation; this pins the WIRING — that the
 * chat bubble partitions its trace instead of feeding both renderers, and that
 * the timeline renders one chip row per run.
 */

import { render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { MessageBubble } from '../chat/AIChatBubble';
import { IssueChatThread } from '../Todolist/IssueChatThread';
import { __clearRunToolActivityCache } from './useRunToolActivity';
import type { ChatToolCall } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Return the English default so assertions read like the UI.
    t: (key: string, fallback?: unknown, opts?: Record<string, unknown>) => {
      if (typeof fallback === 'object' && fallback !== null) {
        const count = (fallback as Record<string, unknown>).count;
        return count === undefined ? key : `${key}:${String(count)}`;
      }
      const count = opts?.count;
      if (count !== undefined) return `${key}:${String(count)}`;
      return typeof fallback === 'string' ? fallback : key;
    },
  }),
}));

vi.mock('../../services/issueMessageService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../services/issueMessageService')>();
  return { ...actual, simulateAgentRunComplete: vi.fn() };
});
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

const getRunEvents = vi.fn();
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    getRunEvents: (...args: unknown[]) => getRunEvents(...args),
  },
}));

const shotResult = JSON.stringify({
  ok: true,
  scene_id: '5',
  shot: {
    shot_id: '900',
    shot_number: 1,
    shot_label: '1-1',
    focal_length: '24mm',
    description: 'Wide on the ridge',
    status: 'empty',
  },
});

beforeEach(() => {
  getRunEvents.mockReset();
  __clearRunToolActivityCache();
});

describe('chat bubble — the trace is partitioned, never rendered twice', () => {
  const calls: ChatToolCall[] = [
    { name: 'ReadScene', iteration: 1, args: {}, result: { ok: true } },
    {
      name: 'CreateShot',
      iteration: 1,
      args: {},
      result: JSON.parse(shotResult) as Record<string, unknown>,
    },
    { name: 'Skill', iteration: 2, args: { skill: 'script-outline' }, result: {} },
  ];

  it('renders each screenwriting call as exactly one chip', () => {
    const { container } = render(
      <MessageBubble role="assistant" content="done" toolCalls={calls} />,
    );
    const chips = container.querySelectorAll('[data-testid="tool-activity-chip"]');
    expect(chips).toHaveLength(2);
    expect([...chips].map((c) => c.getAttribute('data-tool'))).toEqual([
      'ReadScene',
      'CreateShot',
    ]);
  });

  it('leaves the non-screenwriting call to the existing sub-task renderer', () => {
    const { container } = render(
      <MessageBubble role="assistant" content="done" toolCalls={calls} />,
    );
    // Skill must appear as a sub-task card and NOT as a chip — otherwise it
    // would be shown twice.
    const chipTools = [...container.querySelectorAll('[data-testid="tool-activity-chip"]')]
      .map((c) => c.getAttribute('data-tool'));
    expect(chipTools).not.toContain('Skill');
    expect(container.textContent).toContain('script-outline');
  });

  it('summarises the turn\'s writes once, with the shot detail', () => {
    const { container } = render(
      <MessageBubble role="assistant" content="done" toolCalls={calls} />,
    );
    const summaries = container.querySelectorAll('[data-testid="turn-write-summary"]');
    expect(summaries).toHaveLength(1);
    expect(summaries[0].getAttribute('data-shot-count')).toBe('1');

    const rows = container.querySelectorAll('[data-testid="turn-write-shot"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('1-1');
    expect(rows[0].textContent).toContain('Wide on the ridge');
    expect(rows[0].textContent).toContain('24mm');
  });

  it('ships NO undo button — a real undo is not implementable yet', () => {
    const { container } = render(
      <MessageBubble role="assistant" content="done" toolCalls={calls} />,
    );
    const summary = container.querySelector('[data-testid="turn-write-summary"]')!;
    expect(summary.textContent?.toLowerCase()).not.toContain('undo');
  });

  it('renders nothing extra for a turn with no tool calls', () => {
    const { container } = render(
      <MessageBubble role="assistant" content="just talking" toolCalls={[]} />,
    );
    expect(container.querySelector('[data-testid="tool-activity-chips"]')).toBeNull();
    expect(container.querySelector('[data-testid="turn-write-summary"]')).toBeNull();
  });
});

describe('collaboration timeline — one chip row per run', () => {
  const runMsg = {
    id: 'm-run',
    issue_id: 1,
    kind: 'agent_run',
    agent_run_id: '777',
    author_agent_id: 'a1',
    author_user_id: null,
    body: 'Wrote the shots.',
    meta: { status: 'completed' },
    created_at: '2026-08-04T12:00:00Z',
  };

  it('fetches the transcript and renders each tool call once', async () => {
    getRunEvents.mockResolvedValue({
      items: [
        {
          seq: 1,
          event_type: 'tool_call',
          payload: { tool: 'ReadScene', iteration: 1, result: '{"ok":true}' },
          created_at: '',
        },
        {
          seq: 2,
          event_type: 'tool_call',
          payload: { tool: 'CreateShot', iteration: 1, result: shotResult },
          created_at: '',
        },
      ],
      count: 2,
    });

    const { container } = render(
      <IssueChatThread messages={[runMsg as never]} agentsById={{}} />,
    );

    await waitFor(() => {
      expect(
        container.querySelectorAll('[data-testid="tool-activity-chip"]'),
      ).toHaveLength(2);
    });
    expect(getRunEvents).toHaveBeenCalledWith('777', 0);
    expect(
      container.querySelectorAll('[data-testid="turn-write-summary"]'),
    ).toHaveLength(1);
  });

  it('renders shot rows as plain text — no editor to jump to on this route', async () => {
    getRunEvents.mockResolvedValue({
      items: [
        {
          seq: 1,
          event_type: 'tool_call',
          payload: { tool: 'CreateShot', iteration: 1, result: shotResult },
          created_at: '',
        },
      ],
      count: 1,
    });

    const { container } = render(
      <IssueChatThread messages={[runMsg as never]} agentsById={{}} />,
    );
    await waitFor(() => {
      expect(container.querySelector('[data-testid="turn-write-shot"]')).not.toBeNull();
    });
    const row = container.querySelector('[data-testid="turn-write-shot"]')!;
    expect(row.tagName).toBe('DIV');
  });

  it('renders no chips when the run made no tool calls', async () => {
    getRunEvents.mockResolvedValue({
      items: [
        { seq: 1, event_type: 'assistant', payload: { content: 'hi' }, created_at: '' },
      ],
      count: 1,
    });

    const { container } = render(
      <IssueChatThread messages={[runMsg as never]} agentsById={{}} />,
    );
    await waitFor(() => expect(getRunEvents).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="tool-activity-chips"]')).toBeNull();
  });

  it('stays quiet when the run is not visible to this user (404)', async () => {
    getRunEvents.mockRejectedValue(new Error('run not found'));
    const { container } = render(
      <IssueChatThread messages={[runMsg as never]} agentsById={{}} />,
    );
    await waitFor(() => expect(getRunEvents).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="tool-activity-chips"]')).toBeNull();
    // The run row itself still renders — the chips are additive, not load-bearing.
    expect(container.textContent).toContain('Wrote the shots.');
  });
});
