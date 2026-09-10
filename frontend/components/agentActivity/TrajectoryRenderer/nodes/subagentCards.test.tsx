/**
 * Sub-agent cards, inbox variants and the schedule node (harness 2b-2 §5).
 * The three card states are what a person reads to know whether to wait, to
 * go elsewhere, or that it is over — so each is asserted on its own.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChildRunContext, type ChildRunOrigin, type ChildRunState } from '../../../Todolist/childRunContext';
import type { InboxNode, ScheduleNode, StepNode, SubagentChild } from '../foldEvents';
import { InboxNodeView, ScheduleNodeView, SubagentCards } from './builtins';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, opts?: unknown) => {
      const template = typeof fallback === 'string' ? fallback : key;
      const vars = (typeof fallback === 'object' ? fallback : opts) as Record<string, unknown> | undefined;
      return vars
        ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`))
        : template;
    },
  }),
}));

const child = (over: Partial<SubagentChild> = {}): SubagentChild => ({
  key: 'child:1',
  childRunId: '347786145852739',
  taskId: null,
  mode: 'sync',
  subagentType: 'librarian',
  description: 'Find the deck',
  continuedFrom: null,
  status: null,
  costCents: null,
  tokensUsed: null,
  durationMs: null,
  ...over,
});

const step = (children: SubagentChild[]): StepNode => ({
  kind: 'step',
  key: 'step:1:2',
  turn: 1,
  step: 2,
  live: true,
  model: 'm',
  startedAt: null,
  lines: [],
  summary: {
    tools: 0, retries: 0, compactions: 0, outputs: 0,
    todo: null, durationMs: null, costCents: null, finishReason: null,
  },
  children,
});

function withChildRun(node: StepNode, open: (o: ChildRunOrigin) => void) {
  const state: ChildRunState = { current: null, open, close: vi.fn() };
  return (
    <ChildRunContext.Provider value={state}>
      <SubagentCards node={node} />
    </ChildRunContext.Provider>
  );
}

describe('SubagentCards', () => {
  it('draws nothing when the step dispatched no sub-agent', () => {
    const { container } = render(<SubagentCards node={step([])} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('a foreground child still going reads as waiting, with a spinner', () => {
    render(<SubagentCards node={step([child()])} />);
    const card = screen.getByTestId('subagent-card');
    expect(card).toHaveAttribute('data-state', 'running');
    expect(card).toHaveTextContent('Waiting For Result');
    expect(card.querySelector('.animate-spin')).not.toBeNull();
  });

  it('a background child says the result comes back through the inbox', () => {
    render(<SubagentCards node={step([child({ mode: 'async', childRunId: null, taskId: 'tk-9' })])} />);
    const card = screen.getByTestId('subagent-card');
    expect(card).toHaveAttribute('data-state', 'queued');
    expect(card).toHaveTextContent('Result Arrives In The Inbox');
  });

  it('a finished child shows its duration and cost', () => {
    render(<SubagentCards node={step([child({ status: 'completed', costCents: 0.03, durationMs: 8400, tokensUsed: 1200 })])} />);
    const card = screen.getByTestId('subagent-card');
    expect(card).toHaveAttribute('data-state', 'done');
    expect(card).toHaveTextContent('8.4s');
    expect(card).toHaveTextContent('¢0.030');
  });

  it('a continued child is tagged with the run it continues', () => {
    render(<SubagentCards node={step([child({ continuedFrom: '347786145800007' })])} />);
    expect(screen.getByTestId('subagent-continued')).toHaveTextContent('800007');
  });

  it('opening a child hands the panel the whole origin', () => {
    const open = vi.fn();
    render(withChildRun(step([child()]), open));
    fireEvent.click(screen.getByTestId('subagent-open'));
    expect(open).toHaveBeenCalledWith({
      childRunId: '347786145852739',
      parentRunId: null,
      step: 2,
      mode: 'sync',
      subagentType: 'librarian',
      description: 'Find the deck',
    });
  });

  it('a background child with no run yet has nothing to open', () => {
    render(withChildRun(step([child({ mode: 'async', childRunId: null, taskId: 'tk-9' })]), vi.fn()));
    expect(screen.queryByTestId('subagent-open')).toBeNull();
  });
});

const inbox = (over: Partial<InboxNode>): InboxNode => ({
  kind: 'inbox',
  key: 'seq:1',
  inboxKind: 'steer',
  turn: 1,
  step: 4,
  at: null,
  result: null,
  source: null,
  ...over,
});

describe('InboxNodeView', () => {
  it('a sub-agent result names the type and shows the summary', () => {
    render(
      <InboxNodeView
        node={inbox({
          inboxKind: 'subagent_result',
          result: { childRunId: '9', subagentType: 'librarian', description: 'Find the deck', status: 'completed', summary: 'Found 3 decks', costCents: 0.03, tokensUsed: 900 },
        })}
        expanded={false}
      />,
    );
    expect(screen.getByTestId('traj-inbox')).toHaveTextContent('librarian');
    expect(screen.getByTestId('traj-inbox-summary')).toHaveTextContent('Found 3 decks');
  });

  it('a schedule-sourced steer reads as a wake-up, not a comment', () => {
    render(
      <InboxNodeView
        node={inbox({ source: { kind: 'schedule', scheduleId: 'sc-1', createdBy: 'user' } })}
        expanded={false}
      />,
    );
    expect(screen.getByTestId('traj-inbox')).toHaveTextContent('Wake-up');
  });

  it('an ordinary steer keeps the generic copy', () => {
    render(<InboxNodeView node={inbox({})} expanded={false} />);
    expect(screen.getByTestId('traj-inbox')).not.toHaveTextContent('Wake-up');
  });
});

describe('ScheduleNodeView', () => {
  const node: ScheduleNode = { kind: 'schedule', key: 'seq:1', scheduleId: 'sc-2', fireAt: '2026-09-11T01:00:00Z', note: 'check the render' };

  it('shows what the agent scheduled and offers to cancel it', () => {
    render(<ScheduleNodeView node={node} expanded={false} />);
    expect(screen.getByTestId('traj-schedule')).toHaveTextContent('check the render');
    expect(screen.getByTestId('schedule-cancel')).toBeInTheDocument();
  });
});
