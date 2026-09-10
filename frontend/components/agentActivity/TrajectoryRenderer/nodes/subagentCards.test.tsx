/**
 * Sub-agent cards, inbox variants and the schedule node (harness 2b-2 §5).
 * The three card states are what a person reads to know whether to wait, to
 * go elsewhere, or that it is over — so each is asserted on its own.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChildRunContext, type ChildRunOrigin, type ChildRunState } from '../../../Todolist/childRunContext';
import type { InboxNode, ScheduleNode, StepNode, SubagentChild } from '../foldEvents';
import { InboxNodeView, ScheduleNodeView, SubagentCards } from './builtins';

const remove = vi.fn();
vi.mock('../../../../services/schedulesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../../../services/schedulesService')>();
  return { ...mod, schedulesService: { ...mod.schedulesService, remove: (...a: unknown[]) => remove(...a) } };
});

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

beforeEach(() => {
  remove.mockReset().mockResolvedValue(undefined);
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const child = (over: Partial<SubagentChild> = {}): SubagentChild => ({
  key: 'child:1',
  childRunId: '347786145852739',
  taskId: null,
  mode: 'sync',
  subagentType: 'librarian',
  description: 'Find the deck',
  continuedFrom: null,
  status: null,
  summary: null,
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

  it('a finished child reads as Done, with its duration and cost', () => {
    render(<SubagentCards node={step([child({ status: 'completed', costCents: 0.03, durationMs: 8400, tokensUsed: 1200 })])} />);
    const card = screen.getByTestId('subagent-card');
    expect(card).toHaveAttribute('data-state', 'done');
    expect(card).toHaveTextContent('Done');
    expect(card).toHaveTextContent('8.4s');
    expect(card).toHaveTextContent('¢0.030');
  });

  // The whole point of I-1: a child that crashed must not read like one that
  // worked. `status` is the only place that fact exists.
  it('a FAILED child says Failed and wears the danger tone, not the ok tone', () => {
    render(<SubagentCards node={step([child({ status: 'failed', costCents: 0.001, durationMs: 1200 })])} />);
    const card = screen.getByTestId('subagent-card');
    expect(card).toHaveAttribute('data-state', 'failed');
    expect(card).toHaveTextContent('Failed');
    expect(card.className).toMatch(/danger/);
    expect(card.className).not.toMatch(/\bborder-ok-line\b/);
    // still says what it burned — a run that died after ten tool calls cost money
    expect(card).toHaveTextContent('1.2s');
  });

  it.each(['cancelled', 'timeout', 'error'])('treats an unrecognised end state (%s) as failed, never as done', (status) => {
    render(<SubagentCards node={step([child({ status })])} />);
    expect(screen.getByTestId('subagent-card')).toHaveAttribute('data-state', 'failed');
  });

  it('shows the summary the child reported back, and omits the line when there is none', () => {
    const { unmount } = render(<SubagentCards node={step([child({ status: 'completed', summary: 'Found 3 decks' })])} />);
    expect(screen.getByTestId('subagent-summary')).toHaveTextContent('Found 3 decks');
    unmount();
    render(<SubagentCards node={step([child({ status: 'completed' })])} />);
    expect(screen.queryByTestId('subagent-summary')).toBeNull();
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
    // A subagent_result row is only ever written by the background worker.
    expect(screen.getByTestId('traj-inbox')).toHaveTextContent('Background');
    expect(screen.getByTestId('traj-inbox-summary')).toHaveTextContent('Found 3 decks');
  });

  it('a FAILED sub-agent result says so and wears the danger tone', () => {
    render(
      <InboxNodeView
        node={inbox({
          inboxKind: 'subagent_result',
          result: { childRunId: '9', subagentType: 'librarian', description: 'Find the deck', status: 'failed', summary: 'provider error', costCents: 0.001, tokensUsed: 40 },
        })}
        expanded={false}
      />,
    );
    const row = screen.getByTestId('traj-inbox');
    expect(row).toHaveTextContent('Failed');
    expect(row.className).toMatch(/danger/);
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
    expect(screen.getByTestId('traj-schedule-cancel')).toBeInTheDocument();
  });

  it('a cancel that went through takes the button away', async () => {
    remove.mockResolvedValue(undefined);
    render(<ScheduleNodeView node={node} expanded={false} />);
    fireEvent.click(screen.getByTestId('traj-schedule-cancel'));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('sc-2'));
    await waitFor(() => expect(screen.queryByTestId('traj-schedule-cancel')).toBeNull());
    expect(screen.queryByTestId('traj-schedule-cancel-error')).toBeNull();
  });

  // The comment in this component says a cancel that silently did nothing is
  // worse than no button — so the failing branch gets a test, not a comment.
  it('a cancel that FAILED keeps the button and says so', async () => {
    remove.mockRejectedValue(new Error('Schedules API 500'));
    render(<ScheduleNodeView node={node} expanded={false} />);
    fireEvent.click(screen.getByTestId('traj-schedule-cancel'));
    expect(await screen.findByTestId('traj-schedule-cancel-error')).toBeInTheDocument();
    expect(screen.getByTestId('traj-schedule-cancel')).toBeInTheDocument();
  });
});
