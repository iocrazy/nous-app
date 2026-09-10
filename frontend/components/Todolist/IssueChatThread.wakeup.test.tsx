/**
 * harness 2b-2 §5-2: a run a wake-up started says so on its header, and the
 * sub-run panel says where the child came from.
 *
 * Own file because both need `useRunToolActivity` stubbed — the rest of
 * IssueChatThread.test.tsx asserts against the real (empty) hook.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { IssueMessage } from '../../services/issueMessageService';
import { startedByWakeup } from '../../services/issueMessageService';
import { DetachedRunPanel, RunTrajectory, wakeupRunIds } from './IssueChatThread';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as Record<string, unknown> | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));

vi.mock('../agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: () => ({
    events: [{ seq: 1, event_type: 'step_start', payload: { turn: 1, step: 1 }, created_at: '2026-09-10T00:00:00Z', turn: 1, step: 1 }],
    denials: [],
  }),
}));
vi.mock('../agentActivity/useRunForks', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../agentActivity/useRunForks')>();
  return { ...mod, useRunForks: () => [] };
});

// Real wire shapes: `meta` is a JSON object, `schedule_id` a uuid STRING,
// and a wake-up row is an ordinary comment — there is no `author_kind`.
const msg = (over: Partial<IssueMessage>): IssueMessage => ({
  id: 'm1',
  issue_id: 347474243723822,
  kind: 'comment',
  author_user_id: 'u-1',
  author_agent_id: null,
  body: 'check the render',
  meta: {},
  duration_seconds: null,
  agent_run_id: null,
  from_status: null,
  to_status: null,
  created_at: '2026-09-10T00:00:00Z',
  ...over,
}) as IssueMessage;

const wakeupComment = msg({ meta: { source: { kind: 'schedule', schedule_id: '4021d6f2-0d4c-4a3a-9f52-9e0c2d5b7a11', created_by: 'user' } } });
const runRow = msg({ id: 'm2', kind: 'agent_run', agent_run_id: '9', body: null });

describe('startedByWakeup', () => {
  it('is true only for a row a schedule wrote', () => {
    expect(startedByWakeup(wakeupComment)).toBe(true);
    expect(startedByWakeup(msg({}))).toBe(false);
    expect(startedByWakeup(msg({ meta: { source: { kind: 'comment' } } }))).toBe(false);
    expect(startedByWakeup(undefined)).toBe(false);
  });
});

describe('wakeupRunIds', () => {
  it('marks the run that immediately follows a wake-up row', () => {
    expect(wakeupRunIds([wakeupComment, runRow])).toEqual(new Set(['9']));
  });

  it('does not mark a run some other comment started', () => {
    expect(wakeupRunIds([msg({}), runRow])).toEqual(new Set());
  });

  it('does not reach past an intervening row', () => {
    expect(wakeupRunIds([wakeupComment, msg({ id: 'x', body: 'hi' }), runRow])).toEqual(new Set());
  });
});

describe('RunTrajectory — wake-up chip', () => {
  it('shows the chip when the run was started by a wake-up', () => {
    render(<RunTrajectory runId="9" isRunning={false} startedByWakeup />);
    expect(screen.getByTestId('run-wakeup-chip').textContent).toContain('Started By Wake-up');
  });

  it('no chip on an ordinary run', () => {
    render(<RunTrajectory runId="9" isRunning={false} />);
    expect(screen.queryByTestId('run-wakeup-chip')).toBeNull();
  });
});

describe('DetachedRunPanel — sub-run origin header', () => {
  const origin = { childRunId: '347786145852739', parentRunId: '347786145800001', step: 3, mode: 'async' as const, subagentType: 'archivist', description: 'Sweep old runs' };

  it('says which run and step the child came from, and offers the way back', () => {
    const onBack = vi.fn();
    render(<DetachedRunPanel runId="347786145852739" origin={origin} onBack={onBack} />);
    const header = screen.getByTestId('child-run-header').textContent ?? '';
    expect(header).toContain('852739');
    expect(header).toContain('800001');
    expect(header).toContain('step 3');
    expect(header).toContain('Background');
    fireEvent.click(screen.getByTestId('child-run-back'));
    expect(onBack).toHaveBeenCalled();
  });

  it('without an origin it stays the replay panel it already was', () => {
    render(<DetachedRunPanel runId="347786145852739" />);
    expect(screen.queryByTestId('child-run-header')).toBeNull();
  });
});
