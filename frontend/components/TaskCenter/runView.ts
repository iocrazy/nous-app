/**
 * Selectors over `agent_runs.metadata_json.view` / `.cost` — the whole-value
 * projections the backend folds from the run's event log (harness P4 §1-②).
 *
 * Components never touch the JSON shape directly: everything reads through
 * here, so a backend field rename is one edit. Every selector returns `null`
 * for a row without a `view` (rows older than mig 453) — the legacy keys
 * (`todos` / `last_retry` / `turn_end_reason`) are read by the fallbacks in
 * agentRunPresentation.ts during the transition, not here.
 */

export type RunPhase = 'running' | 'compacting' | 'paused' | 'waiting_input' | 'ended';

export interface RunStep {
  done: number;
  total: number;
  label: string | null;
}

export interface RunRetry {
  attempt: number;
  max: number;
  delay_ms: number | null;
  model: string | null;
  /** ISO — stamped by the emitter, never by the fold. */
  at: string | null;
}

export interface RunContext {
  used_pct: number;
  window: number | null;
}

export interface RunBudget {
  pct: number;
  /** wrap_up (phase 2a): the one-step grace a "Wrap up" answer granted. */
  state: 'warn' | 'over' | 'wrap_up';
  spent_cents: number | null;
}

/** The sub-agent counters the backend folds per run (harness 2b-2). */
export interface RunChildren {
  total: number;
  done: number;
  running: number;
  async_pending: number;
  last: { child_run_id: string; subagent_type: string; status: string } | null;
}

/** An armed one-shot wake-up pointing at this run's issue. */
export interface RunWakeup {
  schedule_id: string;
  fire_at: string;
  note: string;
}

export interface RunEnded {
  reason: string;
  [k: string]: unknown;
}

export interface RunView {
  v: number;
  phase: RunPhase | string;
  step: RunStep | null;
  current: { turn: number | null; step: number | null; model: string | null } | null;
  retry: RunRetry | null;
  context: RunContext | null;
  blocked: { code?: string; message?: string } | null;
  /** harness 2b-2 §5-1: sub-agents this run dispatched. */
  children: RunChildren;
  ended: RunEnded | null;
  inbox_pending: number;
  budget: RunBudget | null;
  /** Phase 2a: the open typed question (folded from question_asked, cleared
   *  by question_answered). Read through questionFromRunView. */
  question?: Record<string, unknown> | null;
  /** Phase 2b-1 §2: set only on a forked run — where it branched from. */
  fork?: { of_run_id: number; at_seq: number } | null;
  /** Phase 2b-1 §3: per-run tool timeout gauge. */
  tools?: { timed_out: number; last_timed_out: string | null } | null;
  /** harness 2b-2 §5-2: wake-ups still armed on this run's issue. */
  wakeups?: RunWakeup[] | null;
  revision: number;
}

export interface RunCostStep {
  turn: number | null;
  step: number | null;
  cost_cents: number | null;
  prompt: number | null;
  completion: number | null;
  duration_ms: number | null;
}

export interface RunCost {
  spent_cents: number;
  by_step: RunCostStep[];
  by_model: Record<string, number>;
  budget_cents: number | null;
  pct: number | null;
}

type Meta = Record<string, unknown> | null | undefined;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Rows written before the 2026-09-05 mirror fix hold `view` / `cost` as a
 * jsonb STRING (double-encoded). Parse once so those rows still read. */
function record(v: unknown): Record<string, unknown> | null {
  if (isRecord(v)) return v;
  if (typeof v === 'string') {
    try {
      const parsed = JSON.parse(v);
      return isRecord(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }
  return null;
}

/** The folded view, or null when the row predates it / carries garbage. */
export function selectRunView(meta: Meta): RunView | null {
  const view = record(meta?.view);
  if (!view || typeof view.v !== 'number' || typeof view.phase !== 'string') return null;
  return view as unknown as RunView;
}

export function selectRunCost(meta: Meta): RunCost | null {
  const cost = record(meta?.cost);
  if (!cost || typeof cost.spent_cents !== 'number') return null;
  return cost as unknown as RunCost;
}

export function runPhase(view: RunView | null): RunView['phase'] | null {
  return view?.phase ?? null;
}

/** "3/7 · doing B" — null when the agent keeps no list. */
export function stepProgress(view: RunView | null): RunStep | null {
  const s = view?.step;
  if (!s || typeof s.done !== 'number' || typeof s.total !== 'number') return null;
  if (!Number.isFinite(s.done) || !Number.isFinite(s.total)) return null;
  return { done: s.done, total: s.total, label: typeof s.label === 'string' ? s.label : null };
}

export interface RetryState {
  attempt: number;
  max: number;
  /** Seconds still to wait at `now`; 0 once the backoff has elapsed. */
  waitingSeconds: number;
}

export function retryState(view: RunView | null, now: number): RetryState | null {
  const r = view?.retry;
  if (!r || typeof r.attempt !== 'number' || typeof r.max !== 'number') return null;
  const delayMs = typeof r.delay_ms === 'number' ? r.delay_ms : 0;
  const atMs = typeof r.at === 'string' ? Date.parse(r.at) : NaN;
  const remaining = Number.isFinite(atMs) ? atMs + delayMs - now : 0;
  return { attempt: r.attempt, max: r.max, waitingSeconds: Math.max(0, remaining / 1000) };
}

export function contextGauge(view: RunView | null): RunContext | null {
  const c = view?.context;
  if (!c || typeof c.used_pct !== 'number') return null;
  return { used_pct: c.used_pct, window: typeof c.window === 'number' ? c.window : null };
}

export function budgetState(view: RunView | null): RunBudget | null {
  const b = view?.budget;
  if (!b || typeof b.pct !== 'number') return null;
  if (b.state !== 'warn' && b.state !== 'over' && b.state !== 'wrap_up') return null;
  return { pct: b.pct, state: b.state, spent_cents: typeof b.spent_cents === 'number' ? b.spent_cents : null };
}

export function endedReason(view: RunView | null): string | null {
  const reason = view?.ended?.reason;
  return typeof reason === 'string' ? reason : null;
}

/** What the run is doing right now, for the one-line cockpit. */
/** Phase 2b-1 §3: the run's tool-timeout gauge; null before any timeout is known. */
export function toolsState(view: RunView | null): { timed_out: number; last_timed_out: string | null } | null {
  const t = view?.tools;
  if (!t || typeof t.timed_out !== 'number') return null;
  return { timed_out: t.timed_out, last_timed_out: typeof t.last_timed_out === 'string' ? t.last_timed_out : null };
}

/**
 * The sub-agent gauge, or null when this run dispatched none — the Cockpit
 * cell exists only when there is something to count (same rule as Tools).
 * Missing counters read as 0 rather than hiding the whole cell: a backend
 * that folded `total` but not `running` should still show 1/2, not nothing.
 */
export function childrenState(view: RunView | null): RunChildren | null {
  const c = view?.children;
  if (!c || typeof c.total !== 'number' || !Number.isFinite(c.total) || c.total <= 0) return null;
  const n = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  const last = c.last && typeof c.last === 'object' ? c.last : null;
  return { total: c.total, done: n(c.done), running: n(c.running), async_pending: n(c.async_pending), last };
}

/** Armed wake-ups, soonest first. Rows without a time are dropped: an
 *  undated wake-up has nothing to display and would sort arbitrarily. */
export function wakeupsState(view: RunView | null): RunWakeup[] {
  const raw = view?.wakeups;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((w): w is RunWakeup => !!w && typeof w === 'object' && typeof (w as RunWakeup).fire_at === 'string')
    .slice()
    .sort((a, b) => a.fire_at.localeCompare(b.fire_at));
}

export function currentStep(view: RunView | null): { turn: number | null; step: number | null; model: string | null } | null {
  const c = view?.current;
  if (!c) return null;
  return {
    turn: typeof c.turn === 'number' ? c.turn : null,
    step: typeof c.step === 'number' ? c.step : null,
    model: typeof c.model === 'string' ? c.model : null,
  };
}
