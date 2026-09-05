import { describe, it, expect } from 'vitest';
import {
  AGENT_RUN_SELECT,
  agentDisplayName,
  agentRunToTask,
  retryProgress,
  todoProgress,
  turnEndSubtitle,
  type AgentRunRow,
} from './agentRunPresentation';

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

describe('agentRunToTask — LLM stats in metadata', () => {
  it('carries tokens / cost / model into metadata for the agent card', () => {
    const task = agentRunToTask(
      baseRow({
        status: 'completed',
        output_summary: 'done',
        prompt_tokens: 234,
        completion_tokens: 56,
        cost_cents: 1.5,
        model: 'qwen3-32b',
      }),
    );
    expect(task.metadata).toMatchObject({
      agent_prompt_tokens: 234,
      agent_completion_tokens: 56,
      agent_cost_cents: 1.5,
      agent_model: 'qwen3-32b',
    });
  });
});


// Real PostgREST wire row, copied from a production response for the debug
// user (2026-08-18): the Snowflake id is a JSON *number*, agent_id is a UUID
// string, and the to-one embed is an object keyed `ai_agents`.
const wireRow = (overrides: Partial<AgentRunRow> = {}): AgentRunRow => ({
  id: 340140596649215,
  user_id: 'b2180063-6860-4f97-9785-ad4eede16064',
  status: 'completed',
  trigger: 'chat',
  input_summary: 'What is in this frame?',
  output_summary: 'A solid deep blue field.',
  error_message: null,
  started_at: '2026-08-19T03:15:37.852624+00:00',
  ended_at: '2026-08-19T03:15:53.191108+00:00',
  created_at: '2026-08-19T03:15:37.852624+00:00',
  prompt_tokens: 4824,
  completion_tokens: 421,
  cost_cents: null,
  model: 'doubao-seed-2-0-lite-260428',
  task_id: null,
  agent_id: 'e7abaa05-4628-4dc9-943d-440928f3625a',
  ai_agents: { name: 'Analyze', slug: 'analyze' },
  ...overrides,
});

describe('AGENT_RUN_SELECT — the column contract the badge depends on', () => {
  it('asks for agent_id and the ai_agents embed', () => {
    // Dropping either silently removes every badge while the panel still
    // renders, so pin both.
    expect(AGENT_RUN_SELECT).toMatch(/(^|,)agent_id(,|$)/);
    expect(AGENT_RUN_SELECT).toContain('ai_agents(slug,name)');
  });

  it('never asks ai_agents for display_name — that column does not exist', () => {
    // Verified against production: selecting display_name fails the whole
    // request with PG 42703, taking the agent rows down with it.
    expect(AGENT_RUN_SELECT).not.toContain('display_name');
  });

  it('keeps the columns the existing row mapping reads', () => {
    for (const col of [
      'id', 'user_id', 'status', 'trigger', 'input_summary', 'output_summary',
      'error_message', 'started_at', 'ended_at', 'created_at', 'prompt_tokens',
      'completion_tokens', 'cost_cents', 'model', 'task_id',
    ]) {
      expect(AGENT_RUN_SELECT).toMatch(new RegExp(`(^|,)${col}(,|$)`));
    }
  });
});

describe('agentDisplayName', () => {
  it('prefers the agent name', () => {
    expect(agentDisplayName(wireRow())).toBe('Analyze');
  });

  it('falls back to the slug when the agent has no name', () => {
    expect(agentDisplayName(wireRow({ ai_agents: { name: null, slug: 'analyze' } }))).toBe('analyze');
    expect(agentDisplayName(wireRow({ ai_agents: { name: '  ', slug: 'analyze' } }))).toBe('analyze');
  });

  it('is undefined when there is no embed — a realtime payload, or RLS hiding the agent', () => {
    expect(agentDisplayName(wireRow({ ai_agents: undefined }))).toBeUndefined();
    expect(agentDisplayName(wireRow({ ai_agents: null }))).toBeUndefined();
    expect(agentDisplayName(wireRow({ ai_agents: { name: null, slug: null } }))).toBeUndefined();
  });
});

describe('agentRunToTask — agent attribution', () => {
  it('carries the embedded agent name and id into metadata for the row badge', () => {
    const task = agentRunToTask(wireRow());
    expect(task.metadata).toMatchObject({
      agent_name: 'Analyze',
      agent_id: 'e7abaa05-4628-4dc9-943d-440928f3625a',
    });
  });

  it('uses the cached name for a realtime row (no embed in the payload)', () => {
    const task = agentRunToTask(wireRow({ ai_agents: undefined }), 'Analyze');
    expect(task.metadata.agent_name).toBe('Analyze');
  });

  it('leaves agent_name null when neither embed nor cache can name the agent', () => {
    const task = agentRunToTask(wireRow({ ai_agents: null }));
    expect(task.metadata.agent_name).toBeNull();
  });

  it('normalizes the Snowflake id to a string, whichever shape the wire used', () => {
    // REST (bigIntSafeFetch, 16+ digits) and realtime (raw websocket JSON)
    // disagree on the type; two shapes for one run would double the row.
    expect(agentRunToTask(wireRow({ id: 340140596649215 })).id).toBe('340140596649215');
    expect(agentRunToTask(wireRow({ id: '3401405966492150' })).id).toBe('3401405966492150');
  });
});

describe('turnEndSubtitle', () => {
  it('names the silent endings a "completed" run can hide', () => {
    expect(turnEndSubtitle({ turn_end_reason: 'max_iterations' })).toBe('Stopped at tool limit');
    expect(turnEndSubtitle({ turn_end_reason: 'provider_length' })).toBe('Cut off by model limit');
  });

  it('stays quiet for a natural finish and for rows without the mirror', () => {
    expect(turnEndSubtitle({ turn_end_reason: 'completed' })).toBeNull();
    expect(turnEndSubtitle({})).toBeNull();
    expect(turnEndSubtitle(null)).toBeNull();
    expect(turnEndSubtitle(undefined)).toBeNull();
  });

  it('wins over output_summary on a completed run — the stop reason is the news', () => {
    const t = agentRunToTask(
      baseRow({
        status: 'completed',
        output_summary: 'Drafted three scenes',
        metadata_json: { turn_end_reason: 'max_iterations' },
      }),
    );
    expect(t.subtitle).toBe('Stopped at tool limit');
  });

  it('falls back to output_summary when the turn ended naturally', () => {
    const t = agentRunToTask(
      baseRow({
        status: 'completed',
        output_summary: 'Drafted three scenes',
        metadata_json: { turn_end_reason: 'completed' },
      }),
    );
    expect(t.subtitle).toBe('Drafted three scenes');
  });

  it('selects metadata_json so the mirror actually reaches the row', () => {
    expect(AGENT_RUN_SELECT).toContain('metadata_json');
  });
});

describe('todoProgress', () => {
  const snap = {
    todos: [
      { id: 1, content: 'step A', status: 'completed', active_form: null },
      { id: 2, content: 'step B', status: 'in_progress', active_form: 'doing B' },
    ],
    counts: { total: 7, completed: 3, in_progress: 1 },
  };

  it('renders "3/7 · doing B" from the todo snapshot', () => {
    expect(todoProgress({ todos: snap })).toEqual({ label: 'doing B', done: 3, total: 7 });
  });

  it('falls back to content when active_form is null', () => {
    const s = { ...snap, todos: [{ ...snap.todos[1], active_form: null }] };
    expect(todoProgress({ todos: s })?.label).toBe('step B');
  });

  it('has no label when nothing is in progress', () => {
    const s = { ...snap, todos: [snap.todos[0]] };
    expect(todoProgress({ todos: s })?.label).toBeNull();
  });

  it('returns null when no snapshot ever landed', () => {
    expect(todoProgress({})).toBeNull();
    expect(todoProgress(null)).toBeNull();
    expect(todoProgress(undefined)).toBeNull();
  });

  it('malformed counts render nothing rather than NaN/7', () => {
    expect(todoProgress({ todos: { todos: [], counts: { completed: 3 } } })).toBeNull();
    expect(todoProgress({ todos: { todos: [], counts: { total: '7', completed: 3 } } })).toBeNull();
  });

  it('reaches the task through agentRunToTask metadata', () => {
    const t = agentRunToTask(baseRow({ metadata_json: { todos: snap } }));
    expect(todoProgress(t.metadata)).toEqual({ label: 'doing B', done: 3, total: 7 });
  });
});

describe('retryProgress', () => {
  const at = '2026-08-27T06:00:00.000Z';
  const atMs = Date.parse(at);
  const meta = { last_retry: { attempt: 2, max_retries: 4, delay_ms: 3200, at, model: 'm' } };

  it('reports the remaining wait against the caller clock', () => {
    const p = retryProgress(meta, atMs + 1000);
    expect(p).toEqual({ attempt: 2, max: 4, waitingSeconds: 2.2 });
  });

  it('reads as no wait once the backoff has elapsed', () => {
    expect(retryProgress(meta, atMs + 60_000)?.waitingSeconds).toBe(0);
  });

  it('is null when the run never retried', () => {
    expect(retryProgress({}, atMs)).toBeNull();
    expect(retryProgress({ last_retry: { attempt: '2' } }, atMs)).toBeNull();
  });
});


describe('view-first selectors (mig 453) with legacy fallback', () => {
  const view = { v: 1, phase: 'running', step: { done: 2, total: 5, label: 'B' }, current: null, retry: { attempt: 1, max: 3, delay_ms: 0, model: null, at: null }, context: null, blocked: null, children: { total: 0, done: 0 }, ended: { reason: 'max_iterations' }, inbox_pending: 0, budget: null, revision: 3 };
  it('prefers view over the legacy keys', () => {
    const meta = { view, todos: { todos: [], counts: { total: 9, completed: 9, in_progress: 0 } }, last_retry: { attempt: 9, max_retries: 9 }, turn_end_reason: 'completed' };
    expect(todoProgress(meta)).toEqual({ done: 2, total: 5, label: 'B' });
    expect(retryProgress(meta, 0)).toEqual({ attempt: 1, max: 3, waitingSeconds: 0 });
    expect(turnEndSubtitle(meta)).toBe('Stopped at tool limit');
  });
  it('still reads the legacy keys on rows without view', () => {
    expect(todoProgress({ todos: { todos: [], counts: { total: 4, completed: 1, in_progress: 0 } } })).toEqual({ done: 1, total: 4, label: null });
    expect(turnEndSubtitle({ turn_end_reason: 'awaiting_approval' })).toBe('Waiting for approval');
  });
  it('carries view/cost through to the task metadata', () => {
    const task = agentRunToTask(baseRow({ metadata_json: { view, cost: { spent_cents: 1 } } }));
    expect((task.metadata as Record<string, unknown>).view).toEqual(view);
    expect((task.metadata as Record<string, unknown>).cost).toEqual({ spent_cents: 1 });
  });
});
