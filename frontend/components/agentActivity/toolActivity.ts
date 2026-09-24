/**
 * Canonical view model for "what did the agent actually do this turn".
 *
 * Two surfaces render agent tool calls and they are fed from DIFFERENT
 * places, which is the whole reason this module exists:
 *
 *   - The floating chat panel already has the trace in hand:
 *     ``AIChatMessage.metadata_json.tool_calls`` (ChatToolCall[]), folded in
 *     by ai_library_chat_service on both the buffered and streaming paths.
 *   - The collaboration timeline has only an ``agent_run_id``, so it fetches
 *     ``agent_run_transcript_events`` (mig 397) via GET /runs/{id}/events.
 *
 * Those are two views of ONE emission — AgentRunner appends to
 * ``tool_call_trace`` and calls ``record_event('tool_call', …)`` from the
 * same block. So a surface must pick exactly one source; reading both would
 * render every call twice. ``fromChatToolCalls`` and ``fromTranscriptEvents``
 * are therefore alternatives, never to be merged, and each one dedupes
 * internally so a repeated poll cannot double-append.
 *
 * Everything here is pure — no React, no fetch — so the exactly-once
 * property is unit-testable without mounting anything.
 */

import type { AgentRunEvent } from '../../types';
import type { ChatToolCall } from '../../types/api';
import { judgeToolOk } from './toolOutcome';

/** How a tool call reads to a human: did it look, suggest, or change things. */
export type ToolActivityKind = 'read' | 'write' | 'propose' | 'generate' | 'other';

/** The screenwriting tool set (A4 + A6). Anything outside this map keeps its
 *  existing renderer — see ``isScreenwritingTool``. */
const TOOL_KINDS: Record<string, ToolActivityKind> = {
  ListScenes: 'read',
  ReadScene: 'read',
  CreateShot: 'write',
  UpdateShot: 'write',
  ApplyEdit: 'write',
  ProposeEdit: 'propose',
  GenerateShotImage: 'generate',
};

/** i18n key per tool, e.g. agentActivity.tool.ReadScene -> "Read script scene". */
export const TOOL_LABEL_PREFIX = 'agentActivity.tool.';

/**
 * True when this tool belongs to the screenwriting set and should render as a
 * chip. Delegate / Skill / anything unknown stays with the existing
 * SubTaskCard renderer — the partition is what guarantees a call appears
 * once, in one place, and is never dropped.
 */
export function isScreenwritingTool(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(TOOL_KINDS, name);
}

/** One shot card the agent produced, summarised for display: 镜号 / 一句话 / 焦段. */
export interface ShotCardSummary {
  shotId: string;
  /** Human shot label ("1-3"); falls back to the bare number, then the id. */
  shotLabel: string;
  description: string | null;
  focalLength: string | null;
  sceneId: string | null;
  /** True when this row came from UpdateShot rather than CreateShot. */
  updated: boolean;
}

export interface ToolActivity {
  /** Stable within one run. Dedupe key — see the module docstring. */
  key: string;
  tool: string;
  kind: ToolActivityKind;
  iteration: number;
  ok: boolean;
  /** Model-facing error text when the tool refused; null on success. */
  errorText: string | null;
  /** Shot cards this single call touched (0 or 1 for the current tool set). */
  shots: ShotCardSummary[];
  /**
   * True when the tool's payload could not be parsed (a truncated transcript
   * blob, typically). The chip still renders — we know the call happened —
   * but the detail is unavailable. Never silently dropped.
   */
  detailUnavailable: boolean;
}

/**
 * Coerce a transcript payload field into a record.
 *
 * ``RunRecorder._truncate_payload`` JSON-*stringifies* every nested dict
 * before insert and clips it at 4000 chars, so ``payload.args`` arrives as a
 * string from the transcript and as a real object from the chat metadata.
 * A clipped blob fails JSON.parse — that is expected, and returns null so the
 * caller can flag ``detailUnavailable`` rather than pretending the call
 * didn't happen.
 */
export function coerceRecord(value: unknown): Record<string, unknown> | null {
  if (value == null) return null;
  if (typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }
  return null;
}

function str(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null;
  if (typeof value === 'number') return String(value);
  return null;
}

/** Pull the shot summary out of a CreateShot / UpdateShot result. */
function extractShots(
  tool: string,
  result: Record<string, unknown> | null,
): ShotCardSummary[] {
  if (!result || result.ok === false) return [];
  if (tool !== 'CreateShot' && tool !== 'UpdateShot') return [];

  const shot = coerceRecord(result.shot);
  if (!shot) return [];

  const shotId = str(shot.shot_id);
  if (!shotId) return [];

  const label = str(shot.shot_label) ?? str(shot.shot_number) ?? shotId;
  return [
    {
      shotId,
      shotLabel: label,
      description: str(shot.description),
      focalLength: str(shot.focal_length),
      sceneId: str(result.scene_id) ?? str(shot.scene_id),
      updated: tool === 'UpdateShot',
    },
  ];
}

function buildActivity(
  key: string,
  tool: string,
  iteration: number,
  rawResult: unknown,
  errorCode: string | null,
): ToolActivity {
  const result = coerceRecord(rawResult);
  // A payload that was present but unparseable means the detail was clipped in
  // transit; a payload that was absent entirely (older rows) is not a parse
  // failure, just an empty one.
  const detailUnavailable = rawResult != null && result === null;
  // 口径与 foldEvents.ts 完全一致——判断本身在 toolOutcome.ts，两边共用一个函数，
  // 不允许再各判各的。`result` 不是唯一判据：后端 `tool_error_code` 把三种失败形状
  // 归一到**顶层** `error_code`，其中「非 ok 的 outcome」与「光有 error 键」两种都
  // 不带 `ok:false`。此前这里只看 result，于是被拒绝/没执行的写工具既渲染成成功，
  // 又被 `summarizeWrites` 计进「wrote N cards」——用户去画布上找一张不存在的卡。
  const ok = judgeToolOk({ result, errorCode });
  return {
    key,
    tool,
    kind: TOOL_KINDS[tool] ?? 'other',
    iteration,
    ok,
    // 结果里的散文优先（对模型/用户都更具体）；只有码没有散文时回落到码本身，
    // 这样失败永远说得出原因，不会退化成一个没有理由的红点。
    errorText: ok ? null : (str(result?.error) ?? errorCode),
    shots: extractShots(tool, result),
    detailUnavailable,
  };
}

/**
 * Timeline source: ``agent_run_transcript_events`` rows.
 *
 * ``seq`` is UNIQUE(run_id, seq) at the DB level, so it is the natural
 * dedupe key — an incremental re-poll that re-returns a row cannot produce a
 * second chip.
 */
export function fromTranscriptEvents(events: AgentRunEvent[]): ToolActivity[] {
  const seen = new Map<string, ToolActivity>();
  for (const ev of events ?? []) {
    if (ev?.event_type !== 'tool_call') continue;
    const payload = ev.payload ?? {};
    const tool = str(payload.tool);
    if (!tool) continue;
    const key = `seq:${ev.seq}`;
    if (seen.has(key)) continue;
    const iteration =
      typeof payload.iteration === 'number' ? payload.iteration : 0;
    // `error_code` sits at the payload's TOP level (3c §3.2), beside `result`
    // rather than inside it — `_truncate_payload` only stringifies the nested
    // dicts, so this one is read directly.
    seen.set(
      key,
      buildActivity(key, tool, iteration, payload.result, str(payload.error_code)),
    );
  }
  return [...seen.values()];
}

/**
 * One capability-gate abort surfaced to the user (Task 6, Agent 权限页梳理
 * 立项). Sourced from the "capability_denied" transcript event Task 5 emits.
 * Dedup here is by seq only (a repeated poll of the same row collapses to
 * one entry); the backend is what guarantees at most one such event per
 * tool per turn, so that per-tool uniqueness is inherited, not enforced,
 * by this module.
 */
export interface CapabilityDenial {
  /** Stable within one run — same seq-based dedupe key as ToolActivity. */
  key: string;
  tool: string;
  /** The hook's abort_reason, verbatim (English, not translated). */
  reason: string;
}

/**
 * Timeline + panel source (via useRunToolActivity): "capability_denied"
 * transcript rows. Same UNIQUE(run_id, seq) dedupe key as
 * ``fromTranscriptEvents`` — an incremental re-poll cannot double-count.
 * A payload missing tool or reason is dropped rather than rendered blank.
 */
export function denialsFromTranscriptEvents(events: AgentRunEvent[]): CapabilityDenial[] {
  const seen = new Map<string, CapabilityDenial>();
  for (const ev of events ?? []) {
    if (ev?.event_type !== 'capability_denied') continue;
    const payload = ev.payload ?? {};
    const tool = str(payload.tool);
    const reason = str(payload.reason);
    if (!tool || !reason) continue;
    const key = `seq:${ev.seq}`;
    if (seen.has(key)) continue;
    seen.set(key, { key, tool, reason });
  }
  return [...seen.values()];
}

/**
 * Panel source: the trace already folded into the assistant message.
 *
 * ChatToolCall carries no unique id, so the key is (iteration, ordinal within
 * that iteration) — which is exactly what makes two genuinely distinct calls
 * of the same tool in the same iteration survive as two chips, while a
 * re-render of the same array collapses to one.
 */
export function fromChatToolCalls(calls: ChatToolCall[]): ToolActivity[] {
  const seen = new Map<string, ToolActivity>();
  const perIteration = new Map<number, number>();
  for (const call of calls ?? []) {
    if (typeof call?.name !== 'string' || !call.name) continue;
    const iteration = typeof call.iteration === 'number' ? call.iteration : 0;
    const ordinal = perIteration.get(iteration) ?? 0;
    perIteration.set(iteration, ordinal + 1);
    const key = `it:${iteration}:${ordinal}`;
    if (seen.has(key)) continue;
    // 与 transcript 同一个值：后端每次派发只算一次 `tool_error_code(result)`，
    // 同时写进 trace 与 transcript 事件。这条路径**才是**用户看到「wrote N cards」
    // 的那条（AIChatBubble.tsx:362-370）——没有它，一个排在 AskUser 后面根本没执行
    // 的 CreateShot 会在气泡上渲染成成功并被计进卡片数。
    // 老消息重放时该键缺席，`str()` 给出 null，即退回只看 `result` 的旧判定。
    seen.set(
      key,
      buildActivity(key, call.name, iteration, call.result, str(call.error_code)),
    );
  }
  return [...seen.values()];
}

/** What the per-turn write summary needs to render. */
export interface TurnWriteSummary {
  /** Distinct shot cards touched — a create followed by an update counts once. */
  shots: ShotCardSummary[];
  /** Successful writes that produced no identifiable card (e.g. ApplyEdit). */
  otherWriteCount: number;
}

/**
 * Fold the turn's activities into the "wrote N cards" summary.
 *
 * Deliberately counts DISTINCT shot ids: the agent commonly creates a card
 * and then updates it in the same turn, and reporting that as 2 cards would
 * overstate what the user will find on the canvas.
 */
export function summarizeWrites(activities: ToolActivity[]): TurnWriteSummary {
  const byShot = new Map<string, ShotCardSummary>();
  let otherWriteCount = 0;
  for (const act of activities) {
    if (act.kind !== 'write' || !act.ok) continue;
    if (act.shots.length === 0) {
      otherWriteCount += 1;
      continue;
    }
    for (const shot of act.shots) {
      const existing = byShot.get(shot.shotId);
      if (!existing) {
        byShot.set(shot.shotId, shot);
        continue;
      }
      // Later calls carry fresher field values; keep them, but remember the
      // card was created this turn rather than merely updated.
      byShot.set(shot.shotId, {
        ...shot,
        description: shot.description ?? existing.description,
        focalLength: shot.focalLength ?? existing.focalLength,
        sceneId: shot.sceneId ?? existing.sceneId,
        updated: existing.updated && shot.updated,
      });
    }
  }
  return { shots: [...byShot.values()], otherWriteCount };
}
