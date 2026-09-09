import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AgentRunEvent } from '../../../types';
import { registeredTrajectoryNodeKinds } from './nodes/registry';
import { TrajectoryRenderer } from './TrajectoryRenderer';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown) =>
      arg2 && typeof arg2 === 'object' ? `${key}:${Object.values(arg2 as Record<string, unknown>).join(',')}` : key,
  }),
}));

let seq = 0;
const ev = (event_type: string, payload: Record<string, unknown> = {}, step?: number): AgentRunEvent => ({
  seq: ++seq,
  event_type,
  payload,
  created_at: '2026-09-05T00:00:00Z',
  turn: 1,
  step: step ?? null,
});

const events = [
  ev('user', { content: 'write four lines' }),
  ev('step_start', { turn: 1, step: 1, model: 'm' }, 1),
  ev('step_end', { turn: 1, step: 1, duration_ms: 2100, cost_cents: 0.2 }, 1),
  ev('tool_call', { tool: 'ReadScene', iteration: 1, result: { ok: true } }),
  ev('tool_call', { tool: 'CreateShot', iteration: 1, result: { ok: true } }),
  ev('inbox_claimed', { kind: 'steer', turn: 1, step: 2 }, 2),
  ev('step_start', { turn: 1, step: 2, model: 'm' }, 2),
  ev('tool_call', { tool: 'UpdateShot', iteration: 2, result: { ok: true } }),
];

describe('TrajectoryRenderer', () => {
  it('registers a renderer for every folded node kind', () => {
    expect(registeredTrajectoryNodeKinds()).toEqual(['budget', 'denied', 'error', 'inbox', 'step', 'turn_end', 'user']);
  });

  it('shows only the live step expanded; a finished step is one summary row until clicked', () => {
    render(<TrajectoryRenderer events={events} isRunning />);
    expect(screen.getAllByTestId('traj-step')).toHaveLength(1);
    expect(screen.getAllByTestId('traj-step-live')).toHaveLength(1);
    // finished step 1: collapsed → summary row, no lines
    const step1 = screen.getByTestId('traj-step');
    expect(step1.querySelector('[data-testid="traj-step-lines"]')).toBeNull();
    expect(step1.textContent).toContain('trajectory.tools:2');
    // live step 2: expanded with its one tool line
    const live = screen.getByTestId('traj-step-live');
    expect(live.querySelectorAll('[data-testid="traj-line-tool"]')).toHaveLength(1);
    // inbox node sits between the steps
    expect(screen.getByTestId('traj-inbox').textContent).toContain('trajectory.inboxClaimed:steer');
    // click opens the finished step in place — still one node
    fireEvent.click(step1.querySelector('button')!);
    expect(step1.querySelectorAll('[data-testid="traj-line-tool"]')).toHaveLength(2);
    expect(screen.getAllByTestId('traj-step')).toHaveLength(1);
  });

  it('renders nothing for an empty transcript', () => {
    const { container } = render(<TrajectoryRenderer events={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('TrajectoryRenderer — fork marks (harness 2b-1 §2)', () => {
  it('draws a mark on the step a fork branched at, and jumps to that run', () => {
    document.body.innerHTML = '<div id="run-701"></div>';
    const jumped: string[] = [];
    document.getElementById('run-701')!.scrollIntoView = (() => jumped.push('701')) as never;
    render(<TrajectoryRenderer events={events} isRunning forkMarks={{ 'step:1:2': ['701'] }} />);
    const marks = screen.getAllByTestId('trajectory-fork-mark');
    expect(marks).toHaveLength(1);
    expect(marks[0].getAttribute('data-run')).toBe('701');
    expect(marks[0].closest('[data-testid="traj-step-live"]')).not.toBeNull(); // step 2 is the live one here
    fireEvent.click(marks[0]);
    expect(jumped).toEqual(['701']);
  });
  it('no marks without forks', () => {
    render(<TrajectoryRenderer events={events} isRunning />);
    expect(screen.queryByTestId('trajectory-fork-mark')).toBeNull();
  });
});
