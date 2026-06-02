import { describe, it, expect } from 'vitest';
import { agentRunToTask, type AgentRunRow } from './agentRunPresentation';

const baseRow = (overrides: Partial<AgentRunRow> = {}): AgentRunRow => ({
  id: 'run-1',
  user_id: 'u1',
  status: 'running',
  trigger: 'chat',
  input_summary: 'Is this image good?',
  output_summary: null,
  error_message: null,
  started_at: '2026-06-02T10:00:00Z',
  ended_at: null,
  created_at: '2026-06-02T10:00:00Z',
  ...overrides,
});

describe('agentRunToTask', () => {
  it('maps a running agent run to a processing agent task', () => {
    const t = agentRunToTask(baseRow());
    expect(t.id).toBe('run-1');
    expect(t.task_type).toBe('agent');
    expect(t.status).toBe('processing');
    expect(t.title).toBe('Is this image good?');
    expect(t.started_at).toBe('2026-06-02T10:00:00Z');
  });

  it('maps completed → completed and surfaces the output summary', () => {
    const t = agentRunToTask(
      baseRow({ status: 'completed', output_summary: 'Looks great', ended_at: '2026-06-02T10:01:00Z' }),
    );
    expect(t.status).toBe('completed');
    expect(t.subtitle).toBe('Looks great');
    expect(t.completed_at).toBe('2026-06-02T10:01:00Z');
  });

  it('maps failed and heartbeat_lost → failed, carrying the error', () => {
    expect(agentRunToTask(baseRow({ status: 'failed', error_message: 'boom' })).status).toBe('failed');
    expect(agentRunToTask(baseRow({ status: 'heartbeat_lost' })).status).toBe('failed');
    expect(agentRunToTask(baseRow({ status: 'failed', error_message: 'boom' })).error_msg).toBe('boom');
  });

  it('maps cancelled → cancelled', () => {
    expect(agentRunToTask(baseRow({ status: 'cancelled' })).status).toBe('cancelled');
  });

  it('falls back to a trigger-based title when there is no input summary', () => {
    expect(agentRunToTask(baseRow({ input_summary: null, trigger: 'issue' })).title).toBe('Agent · issue');
    expect(agentRunToTask(baseRow({ input_summary: '   ', trigger: 'chat' })).title).toBe('Agent · chat');
  });

  it('never carries a resource_id (agent runs produce no library resource)', () => {
    expect(agentRunToTask(baseRow()).resource_id).toBeUndefined();
  });
});
