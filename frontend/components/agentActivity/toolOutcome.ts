/**
 * Did one tool call succeed? One answer, shared by both surfaces.
 *
 * Two modules render a tool call's outcome and they used to judge it
 * separately: `toolActivity.ts` (the chat panel's chips + the "wrote N cards"
 * summary) looked only at `result`, while `TrajectoryRenderer/foldEvents.ts`
 * also read the transcript's top-level `error_code`. The chips were therefore
 * the lenient one — a write the runner refused rendered green AND was counted
 * as a card the user would never find on the canvas.
 *
 * The judgement lives here so the two cannot drift apart again.
 *
 * ── Why `error_code` is not redundant with `result.ok` ─────────────────────
 * Backend `tool_error_code` (backend/app/services/ai/runner/tool_events.py)
 * folds three different result shapes onto one top-level field:
 *
 *   1. a handler-supplied `error_code`   — these do also carry `ok: false`
 *   2. an `outcome` that is not "ok"     — no `ok` key at all
 *   3. the bare presence of an `error` key, degraded to "tool_error"
 *                                        — no `ok` key at all
 *
 * Only shape 1 is visible in `result.ok`. Shapes 2 and 3 are real and common
 * (a turn that parks on AskUser drains its queued calls with
 * `{error: "not executed…", skipped: true}`), so `result` alone reads them as
 * clean successes. Both judges are needed — it is not a choice between them.
 *
 * `timed_out` is derived here rather than accepted as an argument: it is a
 * property of the result, and letting each caller compute and pass its own
 * would reopen exactly the divergence this module closes.
 */

/**
 * A wall-clock timeout (harness 2b-1 §3). The typed shape is
 * `{error: "timeout", timed_out: true, timeout_s, elapsed_s}` — note it
 * carries no `ok` key, so it is its own signal, reported independently.
 */
export function toolTimedOut(result: Record<string, unknown> | null): boolean {
  return result !== null && result.timed_out === true;
}

export interface ToolOutcomeInput {
  /** The parsed tool result, or null when absent / truncated past parsing. */
  result: Record<string, unknown> | null;
  /** The transcript event's TOP-LEVEL `error_code`. Null when the source has
   *  no such field at all (ChatToolCall) — never invent one. */
  errorCode: string | null;
}

/**
 * True when the call succeeded on every independent signal.
 *
 * An absent or unparseable result counts as success: the runner only records
 * a `tool_call` for a call it actually executed, so "the detail was clipped
 * in transit" must not be reported as "the tool failed".
 */
export function judgeToolOk({ result, errorCode }: ToolOutcomeInput): boolean {
  return result?.ok !== false && !toolTimedOut(result) && errorCode === null;
}
