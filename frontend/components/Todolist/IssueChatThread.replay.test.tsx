/**
 * harness 2b-1 §1: the replay scrubber attaches to the run named by the
 * context, and in the past the trajectory is events[:seq], frozen.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { IssueChatThread } from './IssueChatThread';
import { ReplayContext } from './replayContext';

vi.mock('../../services/issueMessageService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/issueMessageService')>();
  return { ...actual, simulateAgentRunComplete: vi.fn() };
});
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, f?: unknown) => (typeof f === 'string' ? f : k) }),
}));

const EVENTS = [
  { seq: 1, event_type: 'user', payload: { content: 'go' }, created_at: '' },
  { seq: 2, event_type: 'step_start', payload: { turn: 1, step: 1 }, created_at: '' },
  { seq: 3, event_type: 'tool_call', payload: { tool: 'Skill', result: {} }, created_at: '' },
  { seq: 4, event_type: 'step_start', payload: { turn: 1, step: 2 }, created_at: '' },
  { seq: 5, event_type: 'tool_call', payload: { tool: 'Skill', result: {} }, created_at: '' },
  { seq: 6, event_type: 'step_start', payload: { turn: 1, step: 3 }, created_at: '' },
];
vi.mock('../agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: () => ({ events: EVENTS, denials: [], activities: [], nodes: [], loaded: true }),
}));
// The renderer's own folding is tested elsewhere; here only WHAT it receives.
vi.mock('../agentActivity/TrajectoryRenderer', () => ({
  TrajectoryRenderer: ({ events, isRunning }: { events: unknown[]; isRunning?: boolean }) => (
    <div data-testid="traj" data-count={events.length} data-running={String(!!isRunning)} />
  ),
}));
afterEach(cleanup);

const runMsg = (id: string) =>
  ({
    id: `m-${id}`,
    issue_id: 1,
    kind: 'agent_run',
    author_user_id: null,
    author_agent_id: 'a1',
    agent_run_id: id,
    content: null,
    body: null,
    created_at: '2026-09-09T00:00:00Z',
    meta: { status: 'running' },
  }) as never;

function mount(replay: React.ContextType<typeof ReplayContext>) {
  return render(
    <MemoryRouter>
      <ReplayContext.Provider value={replay}>
        <IssueChatThread messages={[runMsg('r1')]} agentsById={{}} />
      </ReplayContext.Provider>
    </MemoryRouter>,
  );
}

describe('IssueChatThread — replay (harness 2b-1 §1)', () => {
  it('Live: scrubber attached, all events, live step kept', () => {
    mount({ runId: 'r1', seq: null, view: null, cost: null, loading: false, seek: vi.fn() });
    expect(screen.getAllByTestId('replay-tick')).toHaveLength(3);
    expect(screen.getByTestId('traj').getAttribute('data-count')).toBe('6');
    expect(screen.getByTestId('traj').getAttribute('data-running')).toBe('true');
  });

  it('in the past: events[:seq] and the trajectory is frozen', () => {
    mount({ runId: 'r1', seq: 4, view: null, cost: null, loading: false, seek: vi.fn() });
    expect(screen.getByTestId('traj').getAttribute('data-count')).toBe('4');
    expect(screen.getByTestId('traj').getAttribute('data-running')).toBe('false');
    expect(screen.getByTestId('run-trajectory').getAttribute('data-replay-seq')).toBe('4');
  });

  it('a tick click seeks through the context', () => {
    const seek = vi.fn();
    mount({ runId: 'r1', seq: null, view: null, cost: null, loading: false, seek });
    fireEvent.click(screen.getAllByTestId('replay-tick')[1]);
    expect(seek).toHaveBeenCalledWith(4);
  });

  it('another run (not the newest) gets no scrubber and no slicing', () => {
    mount({ runId: 'r-newer', seq: 4, view: null, cost: null, loading: false, seek: vi.fn() });
    expect(screen.queryByTestId('replay-scrubber')).toBeNull();
    expect(screen.getByTestId('traj').getAttribute('data-count')).toBe('6');
  });

  it('no context at all: plain trajectory', () => {
    mount(null);
    expect(screen.queryByTestId('replay-scrubber')).toBeNull();
    expect(screen.getByTestId('traj').getAttribute('data-count')).toBe('6');
  });
});
