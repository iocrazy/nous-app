/**
 * Fold one run's transcript events into trajectory nodes (harness P4 §1-②,
 * seam C's data half). Pure — no React, no fetch.
 *
 * The rule the UI is built on: **steps do not stack.** Everything that
 * happens between one `step_start` and its `step_end` is ONE node whose
 * lines update in place (a second tool call is a second line inside the
 * same node, never a second node). A finished step folds to its one-line
 * summary; only the step that has started and not ended is `live`. Events
 * with no coordinates (rows older than mig 453) fall back to the tool call's
 * `iteration` so old runs still fold into steps instead of a flat list.
 *
 * Unknown event types are skipped: a new backend type never breaks a client.
 */

import type { AgentRunEvent } from '../../../types';

export type StepLineType = 'tool' | 'retry' | 'compaction' | 'todo' | 'output' | 'model';

export interface StepLine {
  key: string;
  type: StepLineType;
  label: string;
  ok: boolean;
  /** Times the same line was updated in place (retries, compactions). */
  count: number;
  durationMs: number | null;
  detail: Record<string, unknown> | null;
}

export interface StepSummary {
  tools: number;
  retries: number;
  compactions: number;
  outputs: number;
  todo: { done: number; total: number } | null;
  durationMs: number | null;
  costCents: number | null;
  finishReason: string | null;
}

/**
 * A sub-agent this step dispatched (harness 2b-2 §5-1). Keyed by whichever
 * identifier the spawn carried: a foreground child has a `child_run_id`
 * immediately, a background one is only a workforce `task_id` until its
 * result comes back.
 */
export interface SubagentChild {
  key: string;
  childRunId: string | null;
  taskId: string | null;
  mode: 'sync' | 'async';
  subagentType: string;
  description: string;
  /** The run this child continues, when it is a continuation. */
  continuedFrom: string | null;
  /** Null = still going (foreground waiting / background queued). Otherwise
   *  the child's own verdict — `completed`, `failed`, … — which the card is
   *  the only place a person ever sees. */
  status: string | null;
  /** What the child reported back, when its result came through the inbox
   *  (background children only — a foreground child answers in-line). */
  summary: string | null;
  costCents: number | null;
  tokensUsed: number | null;
  durationMs: number | null;
}

export interface StepNode {
  kind: 'step';
  key: string;
  turn: number;
  step: number;
  /** Started, not ended — the one expanded activity block. */
  live: boolean;
  model: string | null;
  startedAt: string | null;
  lines: StepLine[];
  summary: StepSummary;
  /** Sub-agents dispatched by this step, in dispatch order. */
  children: SubagentChild[];
}

export interface UserNode {
  kind: 'user';
  key: string;
  text: string;
  at: string | null;
}

/** Where a claimed steer came from, when it did not come from a person typing. */
export interface InboxSource {
  kind: string;
  scheduleId: string | null;
  createdBy: string | null;
}

/** The payload of a `subagent_result` inbox row — a finished background child. */
export interface SubagentResult {
  childRunId: string | null;
  subagentType: string;
  description: string;
  status: string;
  summary: string;
  costCents: number | null;
  tokensUsed: number | null;
}

export interface InboxNode {
  kind: 'inbox';
  key: string;
  inboxKind: string;
  turn: number | null;
  step: number | null;
  at: string | null;
  /** Set only on `kind === 'subagent_result'`. */
  result: SubagentResult | null;
  /** Set when the claimed item names its origin (a schedule, today). */
  source: InboxSource | null;
}

/** An agent-set wake-up (harness 2b-2 §5-2), folded from `schedule_set`. */
export interface ScheduleNode {
  kind: 'schedule';
  key: string;
  scheduleId: string;
  fireAt: string | null;
  note: string;
}

export interface BudgetNode {
  kind: 'budget';
  key: string;
  action: 'warn' | 'halt';
  pct: number | null;
  spentCents: number | null;
  budgetCents: number | null;
}

export interface TurnEndNode {
  kind: 'turn_end';
  key: string;
  reason: string;
  costCents: number | null;
  steps: number;
  at: string | null;
}

export interface DeniedNode {
  kind: 'denied';
  key: string;
  tool: string;
  reason: string;
}

export interface ErrorNode {
  kind: 'error';
  key: string;
  text: string;
}

export type TrajectoryNode =
  | UserNode
  | StepNode
  | InboxNode
  | ScheduleNode
  | BudgetNode
  | TurnEndNode
  | DeniedNode
  | ErrorNode;

export interface FoldOptions {
  /** False once the run has settled — no step may then be live. */
  isRunning?: boolean;
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
/**
 * Nested payload fields arrive as JSON *strings*: the backend's
 * `_truncate_payload` stringifies every nested dict/list before insert (so a
 * huge tool result cannot sneak past the cap). `usage`, `counts`, `todos`,
 * `result` all come this way on the real wire (2026-09-05 真栈验收) — read them
 * through this, never as objects.
 */
const obj = (v: unknown): Record<string, unknown> | null => {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v as Record<string, unknown>;
  if (typeof v === 'string') {
    try {
      const parsed = JSON.parse(v);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }
  return null;
};
const arr = (v: unknown): unknown[] => {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string') {
    try {
      const parsed = JSON.parse(v);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
};
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

/**
 * Every identity a sub-agent event can be keyed by, in preference order.
 * A foreground spawn has a `child_run_id` from the start; a BACKGROUND spawn
 * has only the workforce `task_id` (no run exists yet) while its `done`
 * carries both — so a card is matched on any id the two events share, never
 * on one field alone.
 */
const childKeys = (p: Record<string, unknown>): string[] => {
  const ids = [str(p.child_run_id), str(p.task_id)].filter((v): v is string => v !== null);
  return ids.map((id) => `child:${id}`);
};

function emptySummary(): StepSummary {
  return {
    tools: 0,
    retries: 0,
    compactions: 0,
    outputs: 0,
    todo: null,
    durationMs: null,
    costCents: null,
    finishReason: null,
  };
}

function newStep(turn: number, step: number, model: string | null, at: string | null): StepNode {
  return {
    kind: 'step',
    key: `step:${turn}:${step}`,
    turn,
    step,
    live: true,
    model,
    startedAt: at,
    lines: [],
    summary: emptySummary(),
    children: [],
  };
}

function upsertLine(node: StepNode, key: string, make: () => Omit<StepLine, 'key' | 'count'>): StepLine {
  const existing = node.lines.find((l) => l.key === key);
  if (existing) {
    const next = make();
    existing.label = next.label;
    existing.ok = next.ok;
    existing.durationMs = next.durationMs;
    existing.detail = next.detail;
    existing.count += 1;
    return existing;
  }
  const line: StepLine = { key, count: 1, ...make() };
  node.lines.push(line);
  return line;
}

export function foldEvents(events: AgentRunEvent[], opts: FoldOptions = {}): TrajectoryNode[] {
  const nodes: TrajectoryNode[] = [];
  let current: StepNode | null = null;
  let stepCost = 0;
  let stepCount = 0;

  const closeCurrent = (): void => {
    if (current) current.live = false;
    current = null;
  };

  const lastStep = (): StepNode | null => {
    for (let i = nodes.length - 1; i >= 0; i -= 1) {
      const n = nodes[i];
      if (n.kind === 'step') return n;
    }
    return null;
  };

  /**
   * The step an activity belongs to. Explicit `step` coordinate first, the
   * tool call's `iteration` second (same counter, older rows). The run path
   * records tool calls and the assistant text AFTER `step_end`, so a
   * coordinate matching the step that just ended attaches to it rather than
   * opening a phantom step; a coordinate-less event with nothing open does
   * the same. Only a genuinely new coordinate without a `step_start`
   * (legacy rows) opens a step here.
   */
  const ensureStep = (ev: AgentRunEvent, iteration: number | null): StepNode => {
    const coord = num(ev.step) ?? iteration;
    if (current && (coord === null || coord === current.step)) return current;
    const prev = lastStep();
    if (prev && (coord === null || prev.step === coord)) return prev;
    closeCurrent();
    current = newStep(num(ev.turn) ?? 1, coord ?? stepCount + 1, null, ev.created_at ?? null);
    stepCount += 1;
    nodes.push(current);
    return current;
  };

  for (const ev of events ?? []) {
    if (!ev || typeof ev.seq !== 'number') continue;
    const p = ev.payload ?? {};
    switch (ev.event_type) {
      case 'user':
        nodes.push({ kind: 'user', key: `seq:${ev.seq}`, text: str(p.content) ?? '', at: ev.created_at ?? null });
        break;

      case 'step_start': {
        closeCurrent();
        current = newStep(num(ev.turn) ?? num(p.turn) ?? 1, num(ev.step) ?? num(p.step) ?? stepCount + 1, str(p.model), ev.created_at ?? null);
        stepCount += 1;
        nodes.push(current);
        break;
      }

      case 'step_end': {
        const node = current ?? ensureStep(ev, null);
        node.summary.durationMs = num(p.duration_ms);
        node.summary.costCents = num(p.cost_cents);
        node.summary.finishReason = str(p.finish_reason);
        if (node.summary.costCents !== null) stepCost += node.summary.costCents;
        if (!node.model) node.model = str(p.model);
        closeCurrent();
        break;
      }

      case 'tool_call': {
        const node = ensureStep(ev, num(p.iteration));
        const tool = str(p.tool) ?? 'tool';
        const result = obj(p.result);
        // Phase 2b-1 §3: a wall-clock timeout is a typed tool result
        // ({error:"timeout", timed_out:true, timeout_s, elapsed_s}) — a
        // failure with its own badge, never a generic "failed".
        const timedOut = result !== null && result.timed_out === true;
        const ok = !(result !== null && (result.ok === false || timedOut));
        upsertLine(node, `tool:${ev.seq}`, () => ({
          type: 'tool',
          label: tool,
          ok,
          durationMs: null,
          detail: {
            tool,
            iteration: num(p.iteration),
            timedOut,
            timeoutS: timedOut ? num(result?.timeout_s) : null,
            elapsedS: timedOut ? num(result?.elapsed_s) : null,
          },
        }));
        node.summary.tools += 1;
        break;
      }

      case 'llm_retry': {
        const node = ensureStep(ev, null);
        node.summary.retries += 1;
        const attempt = num(p.attempt);
        const max = num(p.max_retries);
        upsertLine(node, 'retry', () => ({
          type: 'retry',
          label: attempt !== null && max !== null ? `${attempt}/${max}` : '',
          ok: true,
          durationMs: num(p.delay_ms),
          detail: { attempt, max, model: str(p.model) },
        }));
        break;
      }

      case 'compaction_start':
      case 'compaction_summary':
      case 'compaction_end': {
        const node = ensureStep(ev, null);
        if (ev.event_type === 'compaction_end') node.summary.compactions += 1;
        upsertLine(node, 'compaction', () => ({
          type: 'compaction',
          label: ev.event_type,
          ok: ev.event_type !== 'compaction_start',
          durationMs: null,
          detail: { before: num(p.tokens_before), after: num(p.tokens_after), path: str(p.path) },
        }));
        break;
      }

      case 'todo_write': {
        const node = ensureStep(ev, null);
        const counts = obj(p.counts) ?? {};
        const done = num(counts.completed);
        const total = num(counts.total);
        if (done !== null && total !== null) node.summary.todo = { done, total };
        const todos = arr(p.todos) as Array<Record<string, unknown>>;
        const active = todos.find((t) => t?.status === 'in_progress');
        upsertLine(node, 'todo', () => ({
          type: 'todo',
          label: str(active?.active_form) ?? str(active?.content) ?? '',
          ok: true,
          durationMs: null,
          detail: { done, total },
        }));
        break;
      }

      case 'assistant': {
        const node = ensureStep(ev, null);
        const content = str(p.content) ?? '';
        node.summary.outputs += 1;
        upsertLine(node, `output:${ev.seq}`, () => ({
          type: 'output',
          label: content,
          ok: true,
          durationMs: null,
          detail: { chars: content.length },
        }));
        break;
      }

      case 'inbox_claimed': {
        // `content` is a nested payload field — a JSON string on the real
        // wire (see `obj`), an object in tests written against the API shape.
        const content = obj(p.content) ?? {};
        const src = obj(content.source);
        const inboxKind = str(p.kind) ?? 'steer';
        nodes.push({
          kind: 'inbox',
          key: `seq:${ev.seq}`,
          inboxKind,
          turn: num(ev.turn) ?? num(p.turn),
          step: num(ev.step) ?? num(p.step),
          at: ev.created_at ?? null,
          source: src
            ? { kind: str(src.kind) ?? '', scheduleId: str(src.schedule_id), createdBy: str(src.created_by) }
            : null,
          result:
            inboxKind === 'subagent_result'
              ? {
                  childRunId: str(content.child_run_id),
                  subagentType: str(content.subagent_type) ?? 'subagent',
                  description: str(content.description) ?? '',
                  status: str(content.status) ?? 'completed',
                  summary: str(content.summary) ?? '',
                  costCents: num(content.cost_cents),
                  tokensUsed: num(content.tokens_used),
                }
              : null,
        });
        break;
      }

      case 'subagent_spawned': {
        const node = ensureStep(ev, null);
        const key = childKeys(p)[0];
        // A spawn with no identifier draws nothing: better one missing card
        // than a card that cannot be opened.
        if (!key) break;
        if (node.children.some((c) => c.key === key)) break;
        node.children.push({
          key,
          childRunId: str(p.child_run_id),
          taskId: str(p.task_id),
          mode: p.mode === 'async' ? 'async' : 'sync',
          subagentType: str(p.subagent_type) ?? 'subagent',
          description: str(p.description) ?? '',
          continuedFrom: str(p.continued_from),
          status: null,
          summary: null,
          costCents: null,
          tokensUsed: null,
          durationMs: null,
        });
        break;
      }

      case 'subagent_done': {
        const keys = childKeys(p);
        if (keys.length === 0) break;
        // A background child's `done` lands in a LATER step than its spawn —
        // the parent has walked on by the time the result comes back. Search
        // every step, not just the current one.
        for (const n of nodes) {
          if (n.kind !== 'step') continue;
          const child = n.children.find((c) => keys.includes(c.key));
          if (!child) continue;
          child.status = str(p.status) ?? 'completed';
          child.costCents = num(p.cost_cents);
          child.tokensUsed = num(p.tokens_used);
          child.durationMs = num(p.duration_ms);
          // The card's summary line, for the background child whose claim
          // lands in a LATER run and so never pairs here (MH-90/91/92). A
          // fallback, not the source of truth: `stampResultSummaries` still
          // prefers the claim when one did reach this run.
          if (!child.summary) child.summary = str(p.summary) || null;
          if (!child.childRunId) child.childRunId = str(p.child_run_id);
          break;
        }
        break;
      }

      case 'schedule_set': {
        const scheduleId = str(p.schedule_id);
        if (scheduleId) {
          nodes.push({
            kind: 'schedule',
            key: `seq:${ev.seq}`,
            scheduleId,
            fireAt: str(p.fire_at),
            note: str(p.note) ?? '',
          });
        }
        break;
      }

      case 'budget_check': {
        const action = p.action === 'halt' ? 'halt' : 'warn';
        nodes.push({
          kind: 'budget',
          key: `seq:${ev.seq}`,
          action,
          pct: num(p.pct),
          spentCents: num(p.spent_cents),
          budgetCents: num(p.budget_cents),
        });
        break;
      }

      case 'turn_end':
        closeCurrent();
        nodes.push({
          kind: 'turn_end',
          key: `seq:${ev.seq}`,
          reason: str(p.reason) ?? 'completed',
          costCents: stepCost > 0 ? Math.round(stepCost * 10000) / 10000 : null,
          steps: stepCount,
          at: ev.created_at ?? null,
        });
        break;

      case 'capability_denied': {
        const tool = str(p.tool);
        const reason = str(p.reason);
        if (tool && reason) nodes.push({ kind: 'denied', key: `seq:${ev.seq}`, tool, reason });
        break;
      }

      case 'error':
        nodes.push({
          kind: 'error',
          key: `seq:${ev.seq}`,
          text: str(p.message) ?? str(p.kind) ?? 'error',
        });
        break;

      default:
        // unknown / system: not rendered, never fatal
        break;
    }
  }

  if (current && opts.isRunning === false) closeCurrent();
  stampResultSummaries(nodes);
  return nodes;
}

/**
 * Carry each `subagent_result` claim's summary onto the card it belongs to.
 *
 * A second pass, not a case in the loop: the worker files the inbox row
 * BEFORE it writes `subagent_done`, and the parent claims it later still, so
 * the claim can arrive on either side of the event that gives the card its
 * `childRunId`. Matching once at the end is order-independent. A claim whose
 * child is not on screen (paged-out events) stamps nothing — the inbox row
 * still renders on its own.
 */
function stampResultSummaries(nodes: TrajectoryNode[]): void {
  const byRun = new Map<string, SubagentChild>();
  for (const n of nodes) {
    if (n.kind !== 'step') continue;
    for (const c of n.children) if (c.childRunId) byRun.set(c.childRunId, c);
  }
  if (byRun.size === 0) return;
  // Overwrites whatever `subagent_done` left as a fallback: both read the
  // same envelope, and this one belongs to a row the user can open. Guarding
  // on `!child.summary` instead would hand the win to whichever event the
  // loop happened to see first — order-dependence is the bug this pass exists
  // to avoid. `stamped` keeps the FIRST claim's wording when a child somehow
  // has two, and an empty claim never blanks a summary that already reads.
  const stamped = new Set<SubagentChild>();
  for (const n of nodes) {
    if (n.kind !== 'inbox' || !n.result?.childRunId || !n.result.summary) continue;
    const child = byRun.get(n.result.childRunId);
    if (!child || stamped.has(child)) continue;
    child.summary = n.result.summary;
    stamped.add(child);
  }
}

/** The single live step, if any — the one block the UI keeps expanded. */
export function liveStep(nodes: TrajectoryNode[]): StepNode | null {
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    const n = nodes[i];
    if (n.kind === 'step') return n.live ? n : null;
  }
  return null;
}
