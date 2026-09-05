import type { UnifiedTask, TaskStatus } from '../../contexts/TaskManagerContext';
import { coerceRecord } from '../agentActivity/toolActivity';
import { endedReason, retryState, selectRunView, stepProgress } from './runView';

// agent_runs is the universal record for every agent turn (chat + issue +
// scheduled). The Task Center merges those runs in client-side (the two-table
// split — agent_runs vs task_tracking — stays intact on the backend). This
// module maps an agent_runs row to the UnifiedTask shape the panel renders.

/** PostgREST to-one embed of the run's agent (FK agent_runs.agent_id →
 * ai_agents.id). The column is `name`, not `display_name` — asking for
 * display_name makes the whole request fail with PG 42703, taking the agent
 * list down with it. Verified against production with a real user JWT
 * (2026-08-18): the embed is readable under the ai_agents RLS read policy
 * (mig 138: system presets + own + team + project), and returns
 * `{"name": "Analyze", "slug": "analyze"}`. Optional because realtime
 * payloads carry the flat row only — no embed. */
export interface AgentRef {
  slug?: string | null;
  name?: string | null;
}

/** Columns the Task Center fetches from agent_runs, embed included. Lives here
 * next to AgentRunRow so the row type and the column list can't drift apart. */
export const AGENT_RUN_SELECT =
  'id,user_id,status,trigger,input_summary,output_summary,error_message,started_at,ended_at,' +
  'created_at,prompt_tokens,completion_tokens,cost_cents,model,task_id,agent_id,metadata_json,' +
  'ai_agents(slug,name)';

/** Subset of public.agent_runs the Task Center reads. */
export interface AgentRunRow {
  /** Snowflake BIGINT. PostgREST sends it as a JSON *number* (verified in
   * production: `"id":340140596649215`), and supabaseClient's bigIntSafeFetch
   * only quotes integers of 16+ digits — so the same column arrives as a
   * number today and, once ids cross 16 digits, as a string over REST while
   * realtime (a websocket, no bigIntSafeFetch) keeps sending a number.
   * agentRunToTask normalizes with String() so the two sources can't produce
   * two rows for one run. */
  id: string | number;
  user_id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'heartbeat_lost';
  trigger: string;
  input_summary: string | null;
  output_summary: string | null;
  error_message: string | null;
  /** Backend mirrors run-level facts here (todos, turn_end_reason). Absent on
   * older rows and on realtime payloads that predate the column selection. */
  metadata_json?: Record<string, unknown> | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  cost_cents?: number | null;
  model?: string | null;
  /** task_tracking PK when the run executes inside a tracked workflow
   * (mig 282). Task-linked runs are hidden from the Task Center — their
   * task entry already represents them. */
  task_id?: string | null;
  /** ai_agents PK (NOT NULL in DB since mig 145) — the join key the hook
   * caches names by, and the only agent handle a realtime payload carries. */
  agent_id?: string | null;
  /** Present on fetched rows, absent on realtime ones. */
  ai_agents?: AgentRef | null;
}

/** The agent's human-readable name for the row badge: its display name, or its
 * slug when the agent has no name. Undefined when the embed is missing (a
 * realtime row) or unreadable (RLS) — callers must degrade to no badge rather
 * than invent one. */
export function agentDisplayName(run: Pick<AgentRunRow, 'ai_agents'>): string | undefined {
  const name = run.ai_agents?.name?.trim();
  if (name) return name;
  const slug = run.ai_agents?.slug?.trim();
  return slug || undefined;
}

/**
 * How the turn actually ended, for the completed-state subtitle. The backend
 * files one typed `turn_end` per turn (harness phase 2) and mirrors its reason
 * into `metadata_json.turn_end_reason`. "completed" and the reasons that already
 * flip the run to failed/cancelled yield null — the default copy is right for
 * them. Only the endings a "completed" run can hide get their own line: the
 * run says done while the agent was in fact stopped short.
 */
export function turnEndSubtitle(metadata: Record<string, unknown> | null | undefined): string | null {
  // view-first (mig 453 fold), legacy mirror key as the transition fallback
  const reason = endedReason(selectRunView(metadata)) ?? metadata?.turn_end_reason;
  switch (reason) {
    case 'max_iterations':    return 'Stopped at tool limit';
    case 'provider_length':   return 'Cut off by model limit';
    case 'context_rejected':  return 'Rejected: context too large';
    case 'awaiting_approval': return 'Waiting for approval';
    case 'interrupted':       return 'Interrupted';
    default:                  return null;
  }
}

/** One row of the agent's todo list, as the backend snapshots it (whole
 * value per write — never a delta, so a missed event cannot corrupt it). */
export interface AgentTodoItem {
  id: number;
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  active_form: string | null;
}

export interface AgentTodoSnapshot {
  todos: AgentTodoItem[];
  counts: { total: number; completed: number; in_progress: number };
}

export interface TodoProgress {
  /** What the agent is doing right now — active_form, else the item's content. */
  label: string | null;
  done: number;
  total: number;
}

/**
 * "3/7 · doing B" from `metadata_json.todos`. Null when no snapshot ever landed
 * (older runs, agents that never wrote a list) — the card then draws nothing
 * rather than "0/0". Malformed counts are also null: a NaN in the UI is worse
 * than silence.
 */
export function todoProgress(metadata: Record<string, unknown> | null | undefined): TodoProgress | null {
  const fromView = stepProgress(selectRunView(metadata));
  if (fromView) return fromView;
  // Phase-2 rows mirrored these as jsonb STRINGS (double-encoded) — coerce.
  const snap = coerceRecord(metadata?.todos) as Partial<AgentTodoSnapshot> | null;
  const counts = snap?.counts;
  if (!snap || !counts || typeof counts.total !== 'number' || typeof counts.completed !== 'number') {
    return null;
  }
  if (!Number.isFinite(counts.total) || !Number.isFinite(counts.completed)) return null;
  const todos = Array.isArray(snap.todos) ? snap.todos : [];
  const active = todos.find((t) => t?.status === 'in_progress');
  const label = active ? active.active_form || active.content || null : null;
  return { label, done: counts.completed, total: counts.total };
}

export interface RetryProgress {
  attempt: number;
  max: number;
  /** Seconds still to wait at `now`; 0 once the backoff has elapsed. */
  waitingSeconds: number;
}

/**
 * "Retry 2/4 · waiting 3.2s" from `metadata_json.last_retry`. The backend
 * stamps `at` when it files the retry; the wait is aged against the caller's
 * clock so a retry that already happened reads "Retried 2/4", not a wait that
 * never ends. Null when the run never retried.
 */
export function retryProgress(
  metadata: Record<string, unknown> | null | undefined,
  now: number,
): RetryProgress | null {
  const fromView = retryState(selectRunView(metadata), now);
  if (fromView) return fromView;
  const r = coerceRecord(metadata?.last_retry) as
    | { attempt?: unknown; max_retries?: unknown; delay_ms?: unknown; at?: unknown }
    | null;
  if (!r || typeof r.attempt !== 'number' || typeof r.max_retries !== 'number') return null;
  const delayMs = typeof r.delay_ms === 'number' ? r.delay_ms : 0;
  const atMs = typeof r.at === 'string' ? Date.parse(r.at) : NaN;
  const remaining = Number.isFinite(atMs) ? atMs + delayMs - now : 0;
  return { attempt: r.attempt, max: r.max_retries, waitingSeconds: Math.max(0, remaining / 1000) };
}

/** agent_runs has no "queued" state — a run is executing or terminal. */
function mapStatus(status: AgentRunRow['status']): TaskStatus {
  switch (status) {
    case 'running':        return 'processing';
    case 'completed':      return 'completed';
    case 'cancelled':      return 'cancelled';
    case 'failed':
    case 'heartbeat_lost': return 'failed';
  }
}

/**
 * @param cachedAgentName name resolved from the hook's agent_id → name cache,
 *   used for realtime rows (whose payload has no embed).
 */
export function agentRunToTask(run: AgentRunRow, cachedAgentName?: string): UnifiedTask {
  const input = run.input_summary?.trim();
  const agentName = agentDisplayName(run) ?? cachedAgentName;
  const status = mapStatus(run.status);
  return {
    id: String(run.id),
    user_id: run.user_id,
    task_type: 'agent',
    status,
    title: input || `Agent · ${run.trigger}`,
    // While running, the trigger ("chat" / "issue") is the most useful hint;
    // once done, surface the agent's own output summary.
    subtitle:
      status === 'completed'
        ? turnEndSubtitle(run.metadata_json) ?? (run.output_summary || undefined)
        : run.trigger,
    progress: 0,
    error_msg: run.error_message || undefined,
    // Stash LLM stats so the agent result card can render tokens/cost/model +
    // the full input/output without a second fetch.
    metadata: {
      agent_prompt_tokens: run.prompt_tokens ?? null,
      agent_completion_tokens: run.completion_tokens ?? null,
      agent_cost_cents: run.cost_cents ?? null,
      agent_model: run.model ?? null,
      agent_output: run.output_summary ?? null,
      agent_input: run.input_summary ?? null,
      // "Which agent ran what": the row badge reads agent_name, the hook's
      // cache is keyed by agent_id.
      agent_id: run.agent_id ?? null,
      agent_name: agentName ?? null,
      // Phase 2 mirrors (todo snapshot / last retry / turn end). Passed through
      // whole so todoProgress()/retryProgress() read one shape everywhere.
      todos: run.metadata_json?.todos ?? null,
      last_retry: run.metadata_json?.last_retry ?? null,
      turn_end_reason: run.metadata_json?.turn_end_reason ?? null,
      // P4 whole-value projections (mig 453). Selectors in runView.ts read
      // these; the three legacy keys above go once every row carries them.
      view: run.metadata_json?.view ?? null,
      cost: run.metadata_json?.cost ?? null,
    },
    created_at: run.created_at,
    started_at: run.started_at || undefined,
    completed_at: run.ended_at || undefined,
  };
}
