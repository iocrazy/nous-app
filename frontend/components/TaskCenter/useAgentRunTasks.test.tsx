import { renderHook, waitFor, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAgentRunTasks } from './useAgentRunTasks';

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ currentUserId: 'b2180063-6860-4f97-9785-ad4eede16064' }),
}));

const supabaseMock = vi.hoisted(() => ({ current: null as unknown }));
vi.mock('../../supabaseClient', () => ({
  getSupabaseClient: () => supabaseMock.current,
}));

/** Realtime handlers the hook registered, keyed by event. */
type Handler = (payload: { new?: unknown; old?: unknown }) => void;

/**
 * Fake Supabase client. The agent_runs fetch replies with the real PostgREST
 * wire shape (numeric Snowflake id, to-one `ai_agents` embed object); realtime
 * payloads deliberately carry NO embed, which is how the real websocket
 * delivers them — that asymmetry is what the name cache exists for.
 */
function makeSupabase(opts: {
  runs: unknown[];
  agentsById?: Record<string, { slug: string | null; name: string | null }>;
}) {
  const handlers: Record<string, Handler> = {};
  const agentLookups: string[] = [];
  const client = {
    from(table: string) {
      if (table === 'agent_runs') {
        const q: Record<string, unknown> = {};
        const chain = () => q;
        Object.assign(q, {
          select: chain, eq: chain, is: chain, order: chain,
          limit: () => Promise.resolve({ data: opts.runs, error: null }),
        });
        return q;
      }
      // ai_agents single-agent lookup
      let wanted = '';
      const q: Record<string, unknown> = {};
      Object.assign(q, {
        select: () => q,
        eq: (_col: string, val: string) => { wanted = val; agentLookups.push(val); return q; },
        maybeSingle: () =>
          Promise.resolve({ data: opts.agentsById?.[wanted] ?? null, error: null }),
      });
      return q;
    },
    channel() {
      const ch: Record<string, unknown> = {};
      Object.assign(ch, {
        on: (_type: string, cfg: { event: string }, cb: Handler) => {
          handlers[cfg.event] = cb;
          return ch;
        },
        subscribe: () => ch,
      });
      return ch;
    },
    removeChannel: () => {},
  };
  return { client, handlers, agentLookups };
}

const run = (over: Record<string, unknown> = {}) => ({
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
  ...over,
});

afterEach(() => vi.clearAllMocks());

/** Let the initial fetch settle before firing realtime events — it replaces
 * the task list wholesale, so an event racing it would be overwritten. */
const settleInitialFetch = () => act(async () => { await Promise.resolve(); });

describe('useAgentRunTasks — agent attribution', () => {
  it('names rows from the fetched embed', async () => {
    const { client } = makeSupabase({ runs: [run()] });
    supabaseMock.current = client;
    const { result } = renderHook(() => useAgentRunTasks());
    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(result.current[0].metadata.agent_name).toBe('Analyze');
  });

  it('names a realtime row from the cache — its payload has no embed', async () => {
    const { client, handlers } = makeSupabase({ runs: [run()] });
    supabaseMock.current = client;
    const { result } = renderHook(() => useAgentRunTasks());
    await waitFor(() => expect(result.current).toHaveLength(1));

    const live = run({ id: 340140596649999, status: 'running', ai_agents: undefined });
    act(() => handlers.INSERT({ new: live }));
    await waitFor(() => expect(result.current).toHaveLength(2));
    const fresh = result.current.find((t) => t.id === '340140596649999');
    expect(fresh?.metadata.agent_name).toBe('Analyze');
  });

  it('looks the agent up once when a realtime row names an agent not seen yet', async () => {
    const { client, handlers, agentLookups } = makeSupabase({
      runs: [],
      agentsById: { 'de4c5b24-bf78-4d83-9a4c-755f07d8aff1': { slug: 'coordinator', name: 'Coordinator' } },
    });
    supabaseMock.current = client;
    const { result } = renderHook(() => useAgentRunTasks());
    await settleInitialFetch();

    const live = run({
      id: 340140596640001,
      status: 'running',
      agent_id: 'de4c5b24-bf78-4d83-9a4c-755f07d8aff1',
      ai_agents: undefined,
    });
    act(() => handlers.INSERT({ new: live }));
    await waitFor(() => expect(result.current[0]?.metadata.agent_name).toBe('Coordinator'));

    // A second run of the same agent reuses the cache.
    act(() => handlers.UPDATE({ new: { ...live, status: 'completed' } }));
    await waitFor(() => expect(result.current[0]?.status).toBe('completed'));
    expect(agentLookups).toEqual(['de4c5b24-bf78-4d83-9a4c-755f07d8aff1']);
    expect(result.current[0].metadata.agent_name).toBe('Coordinator');
  });

  it('keeps the row when the agent cannot be named', async () => {
    const { client, handlers } = makeSupabase({ runs: [], agentsById: {} });
    supabaseMock.current = client;
    const { result } = renderHook(() => useAgentRunTasks());
    await settleInitialFetch();
    act(() => handlers.INSERT({ new: run({ status: 'running', ai_agents: undefined }) }));
    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(result.current[0].metadata.agent_name).toBeNull();
    expect(result.current[0].title).toBe('What is in this frame?');
  });

  it('drops a run once it is linked to a task_tracking row, matching numeric ids', async () => {
    const { client, handlers } = makeSupabase({ runs: [run()] });
    supabaseMock.current = client;
    const { result } = renderHook(() => useAgentRunTasks());
    await waitFor(() => expect(result.current).toHaveLength(1));
    // Realtime sends the id as a number; the state key is a string.
    act(() => handlers.UPDATE({ new: run({ task_id: '7788' }) }));
    await waitFor(() => expect(result.current).toHaveLength(0));
  });

  it('removes a deleted run despite the numeric id in the delete payload', async () => {
    const { client, handlers } = makeSupabase({ runs: [run()] });
    supabaseMock.current = client;
    const { result } = renderHook(() => useAgentRunTasks());
    await waitFor(() => expect(result.current).toHaveLength(1));
    act(() => handlers.DELETE({ old: { id: 340140596649215 } }));
    await waitFor(() => expect(result.current).toHaveLength(0));
  });
});
