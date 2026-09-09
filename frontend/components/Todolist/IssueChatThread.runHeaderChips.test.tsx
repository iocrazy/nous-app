/**
 * harness 2b-1 §2/§3: a run's row carries "Forked from …" and "N timed out"
 * from its OWN events — they must survive the run ending (the Cockpit's live
 * view does not; real stack 2026-09-09).
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RunTrajectory } from './IssueChatThread';
import { ReplayContext } from './replayContext';

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, f?: unknown, o?: Record<string, unknown>) => {
      const opts = (typeof f === 'object' && f ? f : o) as Record<string, unknown> | undefined;
      const tpl = typeof f === 'string' ? f : k;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, key) => String(opts?.[key] ?? ''));
    },
  }),
}));
let EVENTS: unknown[] = [];
vi.mock('../agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: () => ({ events: EVENTS, denials: [], activities: [], nodes: [], loaded: true }),
}));
vi.mock('../agentActivity/TrajectoryRenderer', () => ({ TrajectoryRenderer: () => <div data-testid="traj" /> }));
vi.mock('../agentActivity/useRunForks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../agentActivity/useRunForks')>();
  return { ...actual, useRunForks: () => [] };
});
afterEach(cleanup);

// Verbatim shapes from production (run 347786145852739 seq 1; MH-74 seq 4).
const FORK_EVENT = { seq: 1, event_type: 'fork', payload: { steer: true, at_seq: 5, of_run_id: 347474259567172 }, created_at: '' };
const TIMEOUT_EVENT = {
  seq: 4,
  event_type: 'tool_call',
  payload: { tool: 'Skill', args: { skill: 'script-outline' }, result: { tool: 'Skill', error: 'timeout', timed_out: true, timeout_s: 0.001, elapsed_s: 0.001 }, iteration: 1 },
  created_at: '',
};
const USER = { seq: 2, event_type: 'user', payload: { content: 'go' }, created_at: '' };

function mount(replay: React.ContextType<typeof ReplayContext>) {
  return render(
    <MemoryRouter>
      <ReplayContext.Provider value={replay}>
        <RunTrajectory runId="r2" isRunning={false} />
      </ReplayContext.Provider>
    </MemoryRouter>,
  );
}
const replayOf = (seekRun = vi.fn()) => ({ runId: 'r9', seq: null, view: null, cost: null, loading: false, seek: vi.fn(), seekRun });

describe('RunTrajectory header chips', () => {
  it('a finished forked run keeps its "Forked from" chip; clicking scrubs the origin run to at_seq', () => {
    EVENTS = [FORK_EVENT, USER];
    const seekRun = vi.fn();
    mount(replayOf(seekRun));
    const chip = screen.getByTestId('run-fork-chip');
    expect(chip.textContent).toContain('Forked from run #567172 @ seq 5');
    fireEvent.click(chip);
    expect(seekRun).toHaveBeenCalledWith('347474259567172', 5);
    expect(screen.queryByTestId('run-timeout-chip')).toBeNull();
  });

  it('a finished run with a timed-out tool shows the count, named after the last tool', () => {
    EVENTS = [USER, TIMEOUT_EVENT];
    mount(replayOf());
    const chip = screen.getByTestId('run-timeout-chip');
    expect(chip.textContent).toBe('1 timed out');
    expect(chip.getAttribute('title')).toBe('Skill');
    expect(screen.queryByTestId('run-fork-chip')).toBeNull();
  });

  it('a root run with no timeouts renders no header row at all', () => {
    EVENTS = [USER, { seq: 3, event_type: 'tool_call', payload: { tool: 'Skill', result: {} }, created_at: '' }];
    mount(replayOf());
    expect(screen.queryByTestId('run-header-chips')).toBeNull();
  });

  it('without a replay context the fork chip is inert (no crash, disabled)', () => {
    EVENTS = [FORK_EVENT];
    mount(null);
    const chip = screen.getByTestId('run-fork-chip') as HTMLButtonElement;
    expect(chip.disabled).toBe(true);
    fireEvent.click(chip);
  });
});
