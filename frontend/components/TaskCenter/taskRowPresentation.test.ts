import { describe, it, expect } from 'vitest';
import {
  taskAgentName,
  taskAwaitingInput,
  taskRowActions,
  taskShowsCover,
} from './taskRowPresentation';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

// Minimal task factory — only the fields the presentation helpers read.
function makeTask(overrides: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id: 't1',
    user_id: 'u1',
    task_type: 'download',
    status: 'completed',
    title: 'Song A',
    progress: 100,
    metadata: {},
    created_at: '2026-06-02T00:00:00Z',
    ...overrides,
  } as UnifiedTask;
}

describe('taskShowsCover', () => {
  it('shows a cover for a completed task that produced a resource', () => {
    expect(taskShowsCover(makeTask({ status: 'completed', resource_id: 'r1' }))).toBe(true);
  });

  it('does not show a cover when there is no resource (e.g. failed parse)', () => {
    expect(taskShowsCover(makeTask({ status: 'failed', resource_id: undefined }))).toBe(false);
  });

  it('does not show a cover while the task is still running', () => {
    expect(taskShowsCover(makeTask({ status: 'processing', resource_id: 'r1' }))).toBe(false);
  });

  it('does not show a cover for a completed task with no resource_id', () => {
    expect(taskShowsCover(makeTask({ status: 'completed', resource_id: undefined }))).toBe(false);
  });
});

describe('taskRowActions', () => {
  it('a completed task with a resource can be opened and downloaded', () => {
    const a = taskRowActions(makeTask({ status: 'completed', resource_id: 'r1' }));
    expect(a).toEqual({ open: true, download: true, retry: false, cancel: false });
  });

  it('a completed task WITHOUT a resource exposes no open/download', () => {
    const a = taskRowActions(makeTask({ status: 'completed', resource_id: undefined }));
    expect(a).toEqual({ open: false, download: false, retry: false, cancel: false });
  });

  it('a running task can only be cancelled', () => {
    const a = taskRowActions(makeTask({ status: 'processing', resource_id: undefined }));
    expect(a).toEqual({ open: false, download: false, retry: false, cancel: true });
  });

  it('a pending task can only be cancelled', () => {
    const a = taskRowActions(makeTask({ status: 'pending' }));
    expect(a).toEqual({ open: false, download: false, retry: false, cancel: true });
  });

  it('a failed task can be retried', () => {
    const a = taskRowActions(makeTask({ status: 'failed', resource_id: undefined }));
    expect(a).toEqual({ open: false, download: false, retry: true, cancel: false });
  });

  it('a cancelled task can be retried', () => {
    const a = taskRowActions(makeTask({ status: 'cancelled' }));
    expect(a).toEqual({ open: false, download: false, retry: true, cancel: false });
  });

  it('a failed task that still produced a resource can be opened AND retried', () => {
    // Defensive: a partial-failure that left a resource behind should still be openable.
    const a = taskRowActions(makeTask({ status: 'failed', resource_id: 'r1' }));
    expect(a).toEqual({ open: true, download: true, retry: true, cancel: false });
  });
});

describe('taskAwaitingInput', () => {
  const marker = { prompt: 'Which ending do you want?', since: '2026-08-03T00:00:00Z', issue_id: 42 };

  it('returns the marker for an active task flagged awaiting_input', () => {
    const t = makeTask({ status: 'processing', metadata: { awaiting_input: marker } });
    expect(taskAwaitingInput(t)).toEqual(marker);
  });

  it('returns null when there is no marker', () => {
    expect(taskAwaitingInput(makeTask({ status: 'processing' }))).toBeNull();
  });

  it('returns null on a terminal task even if a stale marker survives', () => {
    // A cancel/reap race can leave the marker on a terminal row — never show
    // "waiting for your input" on a task that can no longer consume it.
    const t = makeTask({ status: 'cancelled', metadata: { awaiting_input: marker } });
    expect(taskAwaitingInput(t)).toBeNull();
  });

  it('returns null for a malformed marker', () => {
    const t = makeTask({ status: 'processing', metadata: { awaiting_input: 'yes' } });
    expect(taskAwaitingInput(t)).toBeNull();
  });
});


describe('taskAgentName', () => {
  const agentTask = (metadata: Record<string, unknown>) =>
    makeTask({ task_type: 'agent', metadata });

  it('returns the agent name an agent row was attributed to', () => {
    expect(taskAgentName(agentTask({ agent_name: 'Analyze' }))).toBe('Analyze');
  });

  it('has no badge when the agent could not be named', () => {
    // Realtime row before the name lookup lands, or an agent ai_agents RLS
    // hides — no badge beats a made-up one.
    expect(taskAgentName(agentTask({ agent_name: null }))).toBeUndefined();
    expect(taskAgentName(agentTask({ agent_name: '   ' }))).toBeUndefined();
    expect(taskAgentName(agentTask({}))).toBeUndefined();
  });

  it('never badges a non-agent row, even if metadata carries a name', () => {
    expect(
      taskAgentName(makeTask({ task_type: 'download', metadata: { agent_name: 'Analyze' } })),
    ).toBeUndefined();
  });
});
