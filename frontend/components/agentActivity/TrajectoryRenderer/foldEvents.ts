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
import type { CostKind } from '../outputCost';
import { judgeToolOk, toolTimedOut } from '../toolOutcome';

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

/**
 * One version of one object this step registered (harness 3a §5). The card is
 * the only place a person sees that the agent produced something durable, so
 * it carries the whole identity — an id without a kind, or a kind without a
 * version, draws nothing rather than a card that cannot be opened.
 */
export interface OutputCard {
  /** `${kind}:${refId}:${version}` — the same key the backend folds `seen` by. */
  key: string;
  kind: string;
  refId: string;
  version: number;
  /** The version this one replaced; null on a first registration. */
  parentVersion: number | null;
  title: string | null;
  model: string | null;
  costCents: number | null;
  /** Where `costCents` came from: `exact` is the catalogue price the
   *  registration carried (media), `allocated` is this step's spend shared out
   *  (text), `null` is no price at all. */
  costKind: CostKind;
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
  /** Outputs this step registered, in registration order (harness 3a §5). */
  outputs: OutputCard[];
  /**
   * The output versions the turn CITED on its way in (harness 3a T8c 缺陷 4).
   * Only STEP 1 of a turn carries them, and only when the turn cited
   * something — a run recorded before the backend wrote the field has no key
   * at all and renders exactly as it always did.
   *
   * Note the direction: `outputs` is what this step PRODUCED, `citations` is
   * what the person POINTED AT. Same coordinates, opposite arrows.
   */
  citations?: OutputCitation[];
}

/** One cited version. Pinned: the coordinates never follow later revisions. */
export interface OutputCitation {
  key: string;
  kind: string;
  refId: string;
  version: number;
  title: string | null;
  /** The issue the cited version was PRODUCED on (3c §2.4). A citation may now
   *  point at another issue's output — the chain only has to be visible to the
   *  caller — so the chip needs somewhere to say where it came from.
   *
   *  `null` on every citation recorded before 3c and on any chain that answers
   *  to no issue. Absent is not "the same issue": the two look identical on
   *  screen only because a chip naming the issue you are on is not drawn. */
  issueKey: string | null;
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

/**
 * 一段阶段性叙述（3c §4.1）：模型在这一步动手**之前**说的话。不是 step 里的一行，
 * 而是 step 之间的一段正文。最终回答（无 `partial`）不走这里，仍折进它的 step。
 */
export interface NarrationNode {
  kind: 'narration';
  key: string;
  text: string;
  /** 它属于哪一步；坐标缺席的老行是 null。 */
  step: number | null;
  at: string | null;
}

export type TrajectoryNode =
  | UserNode
  | NarrationNode
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
 * `user.referenced_outputs` → the citations one step shows (3a T8c 缺陷 4).
 *
 * An entry short a coordinate is dropped, not drawn: the chip names a version
 * a reader can go look at, and one that cannot be identified says the wrong
 * thing more confidently than saying nothing.
 */
const citationsFrom = (v: unknown): OutputCitation[] => {
  const out: OutputCitation[] = [];
  for (const raw of arr(v)) {
    const item = obj(raw);
    if (!item) continue;
    const kind = str(item.kind);
    const refId = str(item.ref_id);
    const version = num(item.version);
    if (!kind || !refId || version === null) continue;
    out.push({
      key: `${kind}:${refId}:${version}`,
      kind,
      refId,
      version,
      title: str(item.title),
      // `citations_for_transcript` omits the key rather than sending null when
      // there is none, so absent and empty are the same answer here.
      issueKey: str(item.issue_key),
    });
  }
  return out;
};

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
    outputs: [],
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
  /**
   * Citations read off this turn's `user` event, waiting for the step that
   * consumes them. They cannot be attached when the `user` event is folded:
   * the step does not exist yet, and opening one there would leave a phantom
   * that the real `step_start` then closes and duplicates. The FIRST step
   * opened after the event takes them — that is "step 1 of this turn" — and
   * a turn that never opens a step has no step to hang them under.
   */
  let pendingCitations: OutputCitation[] | null = null;

  /** `newStep` plus the pending citations, so every opener gets them once. */
  const openStep = (turn: number, step: number, model: string | null, at: string | null): StepNode => {
    const node = newStep(turn, step, model, at);
    if (pendingCitations) {
      node.citations = pendingCitations;
      pendingCitations = null;
    }
    return node;
  };

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
   * Where that step SITS — the same lookup `stepAt` does, one level down. A
   * narration has to be spliced in *before* its step (3c §4.1), and that
   * needs the position, not the node.
   */
  const stepIndexAt = (turn: number, step: number): number => {
    for (let i = nodes.length - 1; i >= 0; i -= 1) {
      const n = nodes[i];
      if (n.kind === 'step' && n.turn === turn && n.step === step) return i;
    }
    return -1;
  };

  /**
   * An existing step at these coordinates, wherever it sits in the list.
   * A deliverable registered through DBOS can land after a LATER step has
   * already started (the storyboard render finishes long after the turn that
   * asked for it), and it belongs to the step that produced it — not to
   * whichever step happens to be last, and not to a phantom new one.
   */
  const stepAt = (turn: number, step: number): StepNode | null => {
    const i = stepIndexAt(turn, step);
    return i < 0 ? null : (nodes[i] as StepNode);
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
    // An out-of-order event naming a step that already exists goes home to
    // it; only a genuinely unseen coordinate opens a step. Leaves `current`
    // open on purpose — a late row about an old step ends nothing.
    if (coord !== null) {
      const earlier = stepAt(num(ev.turn) ?? 1, coord);
      if (earlier) return earlier;
    }
    closeCurrent();
    current = openStep(num(ev.turn) ?? 1, coord ?? stepCount + 1, null, ev.created_at ?? null);
    stepCount += 1;
    nodes.push(current);
    return current;
  };

  for (const ev of events ?? []) {
    if (!ev || typeof ev.seq !== 'number') continue;
    const p = ev.payload ?? {};
    switch (ev.event_type) {
      case 'user': {
        nodes.push({ kind: 'user', key: `seq:${ev.seq}`, text: str(p.content) ?? '', at: ev.created_at ?? null });
        // Held for the step that consumes them — see `pendingCitations`. An
        // absent key leaves the previous turn's leftovers alone only because
        // there are none: each turn's step takes them the moment it opens.
        const cited = citationsFrom(p.referenced_outputs);
        pendingCitations = cited.length ? cited : null;
        break;
      }

      case 'step_start': {
        closeCurrent();
        current = openStep(num(ev.turn) ?? num(p.turn) ?? 1, num(ev.step) ?? num(p.step) ?? stepCount + 1, str(p.model), ev.created_at ?? null);
        stepCount += 1;
        nodes.push(current);
        break;
      }

      case 'step_end': {
        // Its own step FIRST, by coordinate — the same `stepAt` lookup
        // `deliverable` and `subagent_done` already use (C8). A step_end can
        // arrive after a LATER step has started (the same out-of-order the
        // other two branches were fixed for), and `current ?? …` then files
        // this step's duration, cost and finish reason under whichever step
        // happens to be open: the step that really ran shows no time at all,
        // and the one that is running picks up money it never spent.
        //
        // Columns first, payload second — the fallback `step_start` and the
        // `deliverable` branch (B2) both have, for pre-453 rows whose
        // coordinates live only in the payload.
        const coord = num(ev.step) ?? num(p.step);
        const own =
          coord !== null ? stepAt(num(ev.turn) ?? num(p.turn) ?? 1, coord) : null;
        const node = own ?? current ?? ensureStep(ev, null);
        node.summary.durationMs = num(p.duration_ms);
        node.summary.costCents = num(p.cost_cents);
        node.summary.finishReason = str(p.finish_reason);
        if (node.summary.costCents !== null) stepCost += node.summary.costCents;
        if (!node.model) node.model = str(p.model);
        // A late row about an OLD step ends nothing — same rule `ensureStep`
        // states for out-of-order events. Closing `current` here would strand
        // the live marker on a step the run has already walked past.
        if (node === current) closeCurrent();
        break;
      }

      case 'tool_call': {
        const node = ensureStep(ev, num(p.iteration));
        const tool = str(p.tool) ?? 'tool';
        const result = obj(p.result);
        // Phase 2b-1 §3: a wall-clock timeout is a typed tool result
        // ({error:"timeout", timed_out:true, timeout_s, elapsed_s}) — a
        // failure with its own badge, never a generic "failed".
        const timedOut = toolTimedOut(result);
        // 失败的真来源是**顶层** `error_code`（Task 8）。后端 `tool_error_code` 把三
        // 种形状归一到它：处理方给的 `error_code`、非 `ok` 的 `outcome`、以及光有
        // `error` 键——后两种都不带 `ok:false`。只认 `result.ok` 会把 denied、
        // invalid_args 这类失败读成成功，而动作动词那一行正是靠 `ok` 决定要不要说
        // "failed"（3c §4.1）。两个判据都要，不是二选一。
        // 判断本体在 `../toolOutcome`，与 `toolActivity.ts` 的 chips 共用同一个函数
        // ——3d batch1 Task 4 之前这两处各判各的，chips 那边漏了 `error_code`。
        const ok = judgeToolOk({ result, errorCode: str(p.error_code) });
        upsertLine(node, `tool:${ev.seq}`, () => ({
          type: 'tool',
          label: tool,
          ok,
          // 这一步花了多久（Task 8 起在 payload 顶层）。缺席保持 null，那正是
          // 「这次调用还没回来」的判据——`AIChatPanel` 的 `openTool` 读它。
          durationMs: num(p.duration_ms),
          detail: {
            tool,
            // 动作动词要拿它拼「对象」（3c §4.1）。真 wire 上 `_truncate_payload`
            // 会把嵌套字典字符串化，所以走 `obj()` 而不是直接读。
            args: obj(p.args),
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
        // 3c §4.1：带 `partial` 的是阶段性叙述——模型在这一步动手**之前**说的话。
        // 它不是 step 里的一行，而是一个独立正文节点，插在它所属 step 节点**之前**：
        // `step_start` 先于这条事件到达，step 节点早已在列表里，只能按坐标插队。
        // 光靠 `push` 会把叙述排到动作后面，读起来就成了事后解释。
        if (p.partial === true) {
          const text = str(p.content);
          if (text) {
            const step = num(ev.step) ?? num(p.step);
            const narration: NarrationNode = {
              kind: 'narration',
              key: `narration:${ev.seq}`,
              text,
              step,
              at: ev.created_at ?? null,
            };
            // 坐标齐全由 Task 19 的契约保证。万一缺席（老行、别的写方），没有
            // 「该插哪里」的依据，就按到达顺序追加到末尾——位置靠后好过插错步。
            // 这是有意的降级，`foldEvents.test.ts` 有用例钉住它。
            const at = step === null ? -1 : stepIndexAt(num(ev.turn) ?? num(p.turn) ?? 1, step);
            if (at < 0) nodes.push(narration);
            else nodes.splice(at, 0, narration);
          }
          // 必须在这里返回：落到下面会既画节点又 `ensureStep`，一段叙述就把还没
          // 开始的 step 提前开了出来。
          break;
        }
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
          // lands in a LATER run and so never pairs here (MH-90/91/92). Set
          // flat: precedence lives in `stampResultSummaries`, which overwrites
          // this with the claim's wording when a claim did reach this run.
          child.summary = str(p.summary) || null;
          if (!child.childRunId) child.childRunId = str(p.child_run_id);
          break;
        }
        break;
      }

      case 'deliverable': {
        const kind = str(p.kind);
        const refId = str(p.ref_id);
        const version = num(p.version);
        // A card that cannot be opened is worse than no card: all three or
        // nothing.
        if (!kind || !refId || version === null) break;
        // Its own step first (the registration may cross DBOS and arrive
        // after the run walked on), then whatever step is open, then the last
        // one. A deliverable NEVER opens a step: a coordinate naming a step
        // that has no `step_start` would otherwise close the live step and
        // strand the live marker on an empty phantom node. Only when the run
        // has no step at all does one get made, because the alternative is
        // dropping the card entirely (修复轮 1)。
        // Columns first, payload second — the same fallback `step_start`
        // has (B2). The registry writes both, but a pre-453 row (and any
        // recorder that fell back to `emit`'s positional signature) carries
        // the coordinates ONLY in the payload; without this they land on
        // whatever step is open instead of the one that produced them.
        const coord = num(ev.step) ?? num(p.step);
        const node =
          (coord !== null ? stepAt(num(ev.turn) ?? num(p.turn) ?? 1, coord) : null) ??
          current ??
          lastStep() ??
          ensureStep(ev, null);
        const key = `${kind}:${refId}:${version}`;
        if (node.outputs.some((o) => o.key === key)) break;
        node.outputs.push({
          key,
          kind,
          refId,
          version,
          parentVersion: num(p.parent_version),
          title: str(p.title),
          model: str(p.model),
          costCents: num(p.cost_cents),
          // A price ON the registration is the catalogue one (media). Text
          // rows carry none and get their share in `allocateStepCosts`.
          costKind: num(p.cost_cents) === null ? null : 'exact',
        });
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
  allocateStepCosts(nodes);
  return nodes;
}

/**
 * 把每一步的 step 花费均摊到这一步登记的产出卡上（3b §3.1）。
 *
 * **分母是这一步登记的全部卡，不是「没有价的卡」** —— 后端
 * `load_step_shares` 按 `(run_id, turn, step)` 下所有 `deliverable` 事件数除，
 * 带目录价的媒体那张也算进去。按没有价的卡数除，会让同一个文本版本在线程卡上
 * 是 `¢0.18`、在血缘端点上是 `¢0.09`：两个面对同一笔钱给两个答案，正是这份
 * 共用写法要消灭的东西。
 *
 * 写进去的只有没有自己价的卡 —— 媒体类登记时就带目录价，拿参考值盖掉精确价
 * 是把账做坏。分母与写入范围是两件事，别把它们合成一个 filter。
 *
 * 第二遍而不是 `step_end` 的分支：登记会晚于本步结束到达（见 `stepAt` 的注释），
 * 在分支里算就只覆盖先到的那几张卡，而份额本身又取决于卡的总数。
 */
function allocateStepCosts(nodes: TrajectoryNode[]): void {
  for (const n of nodes) {
    if (n.kind !== 'step' || n.summary.costCents === null) continue;
    if (n.outputs.length === 0) continue;
    const each = n.summary.costCents / n.outputs.length;
    for (const o of n.outputs) {
      if (o.costCents !== null) continue;
      o.costCents = each;
      o.costKind = 'allocated';
    }
  }
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
