/**
 * The exactly-once property lives here.
 *
 * A7's named failure mode is "a tool call renders twice, or vanishes". Both
 * surfaces normalise through this module, so pinning it here pins it for both
 * without mounting either.
 */

import { describe, expect, it } from 'vitest';

import type { AgentRunEvent, ChatToolCall } from '../../types';
import { judgeToolOk, toolTimedOut } from './toolOutcome';
import {
  coerceRecord,
  denialsFromTranscriptEvents,
  fromChatToolCalls,
  fromTranscriptEvents,
  isScreenwritingTool,
  summarizeWrites,
} from './toolActivity';

const shot = (id: string, num: number, desc: string, focal: string | null) => ({
  shot_id: id,
  shot_number: num,
  shot_label: `1-${num}`,
  shot_type: 'CU',
  camera_angle: 'eye',
  camera_movement: 'static',
  focal_length: focal,
  description: desc,
  status: 'empty',
});

function transcriptEvent(
  seq: number,
  tool: string,
  result: unknown,
  iteration = 1,
): AgentRunEvent {
  return {
    seq,
    event_type: 'tool_call',
    // RunRecorder._truncate_payload JSON-stringifies nested dicts, so the wire
    // shape really is a string here. Tests use the true shape on purpose.
    payload: { tool, iteration, result: JSON.stringify(result) },
    created_at: '2026-08-04T00:00:00Z',
  };
}

describe('isScreenwritingTool', () => {
  it('claims the A4 + A6 tool set and nothing else', () => {
    for (const name of [
      'ListScenes', 'ReadScene', 'CreateShot',
      'UpdateShot', 'ProposeEdit', 'ApplyEdit', 'GenerateShotImage',
    ]) {
      expect(isScreenwritingTool(name)).toBe(true);
    }
    // Delegate / Skill keep the existing SubTaskCard renderer. If this ever
    // flips, the chat bubble would render them in BOTH renderers.
    expect(isScreenwritingTool('Delegate')).toBe(false);
    expect(isScreenwritingTool('Skill')).toBe(false);
    // FinishIssue must stay out too, and for a second reason: backend
    // `tool_error_code` stamps even a SUCCESSFUL FinishIssue with a code
    // (its outcomes are completed/needs_input/continue, none of which is the
    // literal "ok"), so a chip would render it failed. `AIChatBubble.tsx:364`
    // filters on this predicate before building activities, which is what
    // keeps that backend quirk off the bubble. Tracked as its own ticket.
    expect(isScreenwritingTool('FinishIssue')).toBe(false);
    expect(isScreenwritingTool('toString')).toBe(false);
  });
});

describe('coerceRecord', () => {
  it('accepts both the object and the JSON-string wire shapes', () => {
    expect(coerceRecord({ ok: true })).toEqual({ ok: true });
    expect(coerceRecord('{"ok":true}')).toEqual({ ok: true });
  });

  it('returns null for a truncated blob rather than throwing', () => {
    // What a >4000-char tool result actually looks like after truncation.
    expect(coerceRecord('{"ok":true,"elements":[{"text":"aaa...')).toBeNull();
  });

  it('returns null for non-record values', () => {
    expect(coerceRecord(null)).toBeNull();
    expect(coerceRecord(42)).toBeNull();
    expect(coerceRecord('[1,2]')).toBeNull();
  });
});

describe('fromTranscriptEvents — exactly once', () => {
  it('renders one activity per tool_call event', () => {
    const acts = fromTranscriptEvents([
      transcriptEvent(1, 'ReadScene', { ok: true }),
      transcriptEvent(2, 'CreateShot', { ok: true, shot: shot('9', 1, 'Wide', '24mm') }),
    ]);
    expect(acts).toHaveLength(2);
    expect(acts.map((a) => a.tool)).toEqual(['ReadScene', 'CreateShot']);
  });

  it('does not double-count when a poll re-returns the same seq', () => {
    const first = transcriptEvent(1, 'ReadScene', { ok: true });
    const acts = fromTranscriptEvents([first, { ...first }, first]);
    expect(acts).toHaveLength(1);
  });

  it('keeps two genuinely distinct calls of the same tool', () => {
    const acts = fromTranscriptEvents([
      transcriptEvent(1, 'ReadScene', { ok: true }),
      transcriptEvent(2, 'ReadScene', { ok: true }),
    ]);
    expect(acts).toHaveLength(2);
    expect(new Set(acts.map((a) => a.key)).size).toBe(2);
  });

  it('ignores non-tool_call events so the chip row stays tools-only', () => {
    const acts = fromTranscriptEvents([
      { seq: 1, event_type: 'user', payload: { content: 'hi' }, created_at: '' },
      { seq: 2, event_type: 'assistant', payload: { content: 'ok' }, created_at: '' },
      transcriptEvent(3, 'ReadScene', { ok: true }),
    ]);
    expect(acts).toHaveLength(1);
    expect(acts[0].tool).toBe('ReadScene');
  });

  it('still renders a chip when the result was truncated, flagged as such', () => {
    const acts = fromTranscriptEvents([
      {
        seq: 1,
        event_type: 'tool_call',
        payload: { tool: 'ReadScene', iteration: 1, result: '{"ok":true,"elem...' },
        created_at: '',
      },
    ]);
    // Dropping it would be the "vanishes" half of the failure mode.
    expect(acts).toHaveLength(1);
    expect(acts[0].detailUnavailable).toBe(true);
    expect(acts[0].ok).toBe(true);
  });

  it('marks a refused call as failed and carries its reason', () => {
    const acts = fromTranscriptEvents([
      transcriptEvent(1, 'CreateShot', {
        ok: false,
        error: 'could not create the shot',
        error_code: 'write_failed',
      }),
    ]);
    expect(acts[0].ok).toBe(false);
    expect(acts[0].errorText).toBe('could not create the shot');
    expect(acts[0].shots).toEqual([]);
  });
});

describe('fromChatToolCalls — exactly once', () => {
  const call = (name: string, iteration: number, result: object): ChatToolCall => ({
    name, iteration, args: {}, result: result as Record<string, unknown>,
  });

  it('renders one activity per call', () => {
    const acts = fromChatToolCalls([
      call('ReadScene', 1, { ok: true }),
      call('CreateShot', 2, { ok: true, shot: shot('9', 1, 'Wide', '24mm') }),
    ]);
    expect(acts).toHaveLength(2);
  });

  it('keeps two identical calls made in the same iteration', () => {
    // Same tool, same iteration, same args — two real calls, two chips.
    const acts = fromChatToolCalls([
      call('ReadScene', 1, { ok: true }),
      call('ReadScene', 1, { ok: true }),
    ]);
    expect(acts).toHaveLength(2);
    expect(new Set(acts.map((a) => a.key)).size).toBe(2);
  });

  it('agrees with the transcript adapter on the same underlying turn', () => {
    // The two sources are two views of ONE emission; a surface picks one.
    // They must describe the turn identically or the panel and the timeline
    // would disagree about what the agent did.
    const fromChat = fromChatToolCalls([
      call('ReadScene', 1, { ok: true }),
      call('CreateShot', 1, { ok: true, shot: shot('9', 1, 'Wide', '24mm') }),
    ]);
    const fromEvents = fromTranscriptEvents([
      transcriptEvent(1, 'ReadScene', { ok: true }, 1),
      transcriptEvent(2, 'CreateShot', { ok: true, shot: shot('9', 1, 'Wide', '24mm') }, 1),
    ]);
    const strip = (a: { key: string }) => ({ ...a, key: '' });
    expect(fromChat.map(strip)).toEqual(fromEvents.map(strip));
  });

  it('skips malformed entries instead of throwing', () => {
    const acts = fromChatToolCalls([
      { name: '', iteration: 1, args: {}, result: {} },
      call('ReadScene', 1, { ok: true }),
    ] as ChatToolCall[]);
    expect(acts).toHaveLength(1);
  });
});

describe('summarizeWrites', () => {
  it('counts only successful writes', () => {
    const acts = fromTranscriptEvents([
      transcriptEvent(1, 'ReadScene', { ok: true }),
      transcriptEvent(2, 'CreateShot', { ok: true, shot: shot('9', 1, 'Wide', '24mm') }),
      transcriptEvent(3, 'CreateShot', { ok: false, error: 'nope' }),
    ]);
    const summary = summarizeWrites(acts);
    expect(summary.shots).toHaveLength(1);
    expect(summary.shots[0]).toMatchObject({
      shotId: '9', shotLabel: '1-1', description: 'Wide', focalLength: '24mm',
    });
  });

  it('counts a shot created then updated in one turn as ONE card', () => {
    // Overstating this would send the user hunting for a card that isn't there.
    const acts = fromTranscriptEvents([
      transcriptEvent(1, 'CreateShot', { ok: true, shot: shot('9', 1, 'Wide', null) }),
      transcriptEvent(2, 'UpdateShot', { ok: true, shot: shot('9', 1, 'Wide on the ridge', '35mm') }),
    ]);
    const summary = summarizeWrites(acts);
    expect(summary.shots).toHaveLength(1);
    expect(summary.shots[0].description).toBe('Wide on the ridge');
    expect(summary.shots[0].focalLength).toBe('35mm');
    // Created this turn, not merely updated.
    expect(summary.shots[0].updated).toBe(false);
  });

  it('counts scene edits separately from shot cards', () => {
    const acts = fromTranscriptEvents([
      transcriptEvent(1, 'ApplyEdit', { ok: true, applied: true }),
      transcriptEvent(2, 'CreateShot', { ok: true, shot: shot('9', 1, 'Wide', '24mm') }),
    ]);
    const summary = summarizeWrites(acts);
    expect(summary.shots).toHaveLength(1);
    expect(summary.otherWriteCount).toBe(1);
  });

  it('treats reads and proposals as non-writes', () => {
    const acts = fromTranscriptEvents([
      transcriptEvent(1, 'ListScenes', { ok: true, count: 3 }),
      transcriptEvent(2, 'ProposeEdit', { ok: true }),
      transcriptEvent(3, 'GenerateShotImage', { ok: true, dispatched: true, shot_id: '9' }),
    ]);
    expect(summarizeWrites(acts)).toEqual({ shots: [], otherWriteCount: 0 });
  });
});

/**
 * Task 6 (Agent 权限页梳理立项): the gate's abort now lands as a
 * "capability_denied" transcript event (Task 5, one per tool per turn). This
 * pins the same seq-dedup normalisation `fromTranscriptEvents` already has,
 * for the sibling event type.
 */
function denialEvent(
  seq: number,
  payload: Record<string, unknown>,
): AgentRunEvent {
  return {
    seq,
    event_type: 'capability_denied',
    payload,
    created_at: '2026-08-10T00:00:00Z',
  };
}

describe('denialsFromTranscriptEvents', () => {
  it('extracts tool + reason from a capability_denied event', () => {
    const denials = denialsFromTranscriptEvents([
      denialEvent(1, { tool: 'CreateShot', reason: 'write_level=none blocks CreateShot' }),
    ]);
    expect(denials).toEqual([
      { key: 'seq:1', tool: 'CreateShot', reason: 'write_level=none blocks CreateShot' },
    ]);
  });

  it('skips events missing tool or reason, and ignores non-denial event types', () => {
    const denials = denialsFromTranscriptEvents([
      denialEvent(1, { reason: 'missing tool' }),
      denialEvent(2, { tool: 'CreateShot' }),
      transcriptEvent(3, 'CreateShot', { ok: true }), // event_type: tool_call
    ]);
    expect(denials).toEqual([]);
  });

  it('dedupes a repeated poll by seq, keeping the run to exactly one notice per tool', () => {
    const denials = denialsFromTranscriptEvents([
      denialEvent(1, { tool: 'CreateShot', reason: 'blocked' }),
      denialEvent(1, { tool: 'CreateShot', reason: 'blocked' }),
    ]);
    expect(denials).toHaveLength(1);
  });
});


describe('toolActivity — timeouts (harness 2b-1 §3)', () => {
  it('a timed-out result is not a success on the chips either', async () => {
    const { fromTranscriptEvents } = await import('./toolActivity');
    const acts = fromTranscriptEvents([
      { seq: 1, event_type: 'tool_call', payload: { tool: 'ReadScene', iteration: 1, result: { error: 'timeout', timed_out: true, timeout_s: 60, elapsed_s: 60 } }, created_at: '' },
    ] as never);
    expect(acts).toHaveLength(1);
    expect(acts[0].ok).toBe(false);
  });
});

/**
 * 3d batch1 Task 4 — the chips must read the **top-level** `error_code`.
 *
 * Backend `tool_error_code` (backend/app/services/ai/runner/tool_events.py)
 * normalises three shapes onto that one field: a handler-supplied
 * `error_code`, a non-`ok` `outcome`, and the bare presence of an `error` key
 * (degraded to `tool_error`). Only the FIRST of those also tends to carry
 * `ok: false` — so judging on `result` alone reads the other two as
 * successes. `foldEvents.ts` has judged both since Task 8; this file did not,
 * which is why a write that never ran still landed in "wrote N cards".
 *
 * Fixtures below are the real wire shapes, not idealised ones (CLAUDE.md
 * 「边界 mock 必须用真实 JSON 形状」): `result` arrives JSON-*stringified*
 * because `RunRecorder._truncate_payload` stringifies nested dicts, while
 * `error_code` sits un-nested at the payload's top level.
 */
function codedEvent(
  seq: number,
  tool: string,
  result: unknown,
  errorCode: string | null,
  iteration = 1,
): AgentRunEvent {
  return {
    seq,
    event_type: 'tool_call',
    payload: { tool, iteration, result: JSON.stringify(result), error_code: errorCode },
    created_at: '2026-09-22T00:00:00Z',
  };
}

describe('toolActivity — top-level error_code (3d batch1 Task 4)', () => {
  // agent_runner.py::_drain_unexecuted — the turn parked on AskUser, so the
  // queued CreateShot never ran. The result carries NO `ok` key, so
  // `tool_error_code` degrades it to "tool_error".
  const unexecuted = {
    error: 'not executed: the turn parked on AskUser before this call',
    skipped: true,
  };

  it('a write that never executed is not ok', () => {
    const acts = fromTranscriptEvents([codedEvent(1, 'CreateShot', unexecuted, 'tool_error')]);
    expect(acts[0].ok).toBe(false);
    expect(acts[0].errorText).toBe(
      'not executed: the turn parked on AskUser before this call',
    );
  });

  it('...and is not counted in "wrote N cards"', () => {
    // The user-visible half of the defect: the panel claimed a card the
    // canvas never got. `otherWriteCount` is where a shot-less write lands.
    const acts = fromTranscriptEvents([codedEvent(1, 'CreateShot', unexecuted, 'tool_error')]);
    expect(summarizeWrites(acts)).toEqual({ shots: [], otherWriteCount: 0 });
  });

  it('an invalid_args refusal is not ok and writes nothing', () => {
    // screenwriting_tools.py::ApplyEdit — this shape carries both signals.
    const acts = fromTranscriptEvents([
      codedEvent(
        1,
        'ApplyEdit',
        {
          ok: false,
          error: 'edits must be a list of {element_id, text} objects with both fields set.',
          error_code: 'invalid_args',
        },
        'invalid_args',
      ),
    ]);
    expect(acts[0].ok).toBe(false);
    expect(summarizeWrites(acts)).toEqual({ shots: [], otherWriteCount: 0 });
  });

  it('falls back to the code when the result carries no error prose', () => {
    // finish_issue_tool.py's accepted shape: no `ok`, no `error`, and an
    // `outcome` that is not the literal "ok" — which is exactly what
    // `tool_error_code` promotes to the top-level code.
    const acts = fromTranscriptEvents([
      codedEvent(
        1,
        'ReadScene',
        { acknowledged: true, outcome: 'needs_input', reason: 'Which ending do you want?' },
        'needs_input',
      ),
    ]);
    expect(acts[0].ok).toBe(false);
    expect(acts[0].errorText).toBe('needs_input');
  });

  it('a successful call with error_code absent or null stays ok', () => {
    const acts = fromTranscriptEvents([
      codedEvent(1, 'CreateShot', { ok: true, shot: shot('9', 1, 'Wide', '24mm') }, null),
      transcriptEvent(2, 'UpdateShot', { ok: true, shot: shot('8', 2, 'Close', '85mm') }),
    ]);
    expect(acts.map((a) => a.ok)).toEqual([true, true]);
    expect(summarizeWrites(acts).shots).toHaveLength(2);
  });

  it('a successful chat tool call stays ok', () => {
    const acts = fromChatToolCalls([
      { name: 'CreateShot', iteration: 1, args: {}, result: { ok: true, shot: shot('9', 1, 'Wide', '24mm') } },
      { name: 'ReadScene', iteration: 1, args: {}, result: { ok: true } },
    ]);
    expect(acts.map((a) => a.ok)).toEqual([true, true]);
    expect(summarizeWrites(acts).shots).toHaveLength(1);
  });

  it('a trace entry predating error_code (absent key) is still judged on result', () => {
    // Assistant messages persisted before the backend added the field replay
    // with no `error_code` at all. Absent must read as "no code", never as a
    // failure — otherwise every historical turn turns red on refetch.
    const acts = fromChatToolCalls([
      { name: 'CreateShot', iteration: 1, args: {}, result: { ok: true, shot: shot('9', 1, 'Wide', '24mm') } },
    ]);
    expect(acts[0].ok).toBe(true);
    expect(summarizeWrites(acts).shots).toHaveLength(1);
  });
});

/**
 * The bubble path — the ONE place the user actually reads "wrote N cards".
 *
 * `AIChatBubble.tsx:362-370` folds `ChatResponse.tool_calls` through
 * `fromChatToolCalls` → `summarizeWrites`; nothing destructures
 * `useRunToolActivity`'s `activities`. So a fix that only reaches the
 * transcript adapter does not reach the count the user sees. Fixtures here are
 * the real `ChatResponse.tool_calls` wire shape (backend `ChatToolCall`:
 * name / iteration / args / result / error_code), NOT the transcript's
 * JSON-stringified `result`.
 */
describe('the chat bubble path honours error_code (3d batch1 Task 4, fix round 1)', () => {
  const unexecutedCreateShot: ChatToolCall = {
    name: 'CreateShot',
    iteration: 1,
    args: { scene_id: '9', description: 'Wide on the ridge' },
    result: {
      error: 'not executed: the turn parked on AskUser before this call',
      skipped: true,
    },
    error_code: 'tool_error',
  };

  it('an unexecuted write is not ok', () => {
    const acts = fromChatToolCalls([unexecutedCreateShot]);
    expect(acts[0].ok).toBe(false);
    expect(acts[0].errorText).toBe(
      'not executed: the turn parked on AskUser before this call',
    );
  });

  it('...and the bubble does not claim a card that was never written', () => {
    expect(summarizeWrites(fromChatToolCalls([unexecutedCreateShot]))).toEqual({
      shots: [],
      otherWriteCount: 0,
    });
  });

  it('a real write in the same turn still counts', () => {
    // The count must not collapse to "nothing ever counts" — one genuine card
    // alongside one refused call reads as exactly one card.
    const acts = fromChatToolCalls([
      unexecutedCreateShot,
      {
        name: 'CreateShot',
        iteration: 2,
        args: {},
        result: { ok: true, shot: shot('9', 1, 'Wide', '24mm') },
        error_code: null,
      },
    ]);
    expect(acts.map((a) => a.ok)).toEqual([false, true]);
    expect(summarizeWrites(acts).shots).toHaveLength(1);
  });

  it('agrees with the transcript adapter on the same underlying call', () => {
    // Two views of ONE emission (module docstring). Now that both carry the
    // code, they must reach the same verdict — that is the whole point of the
    // backend change.
    const fromChat = fromChatToolCalls([unexecutedCreateShot]);
    const fromEvents = fromTranscriptEvents([
      codedEvent(1, 'CreateShot', unexecutedCreateShot.result, 'tool_error'),
    ]);
    expect(fromChat[0].ok).toBe(fromEvents[0].ok);
    expect(fromChat[0].errorText).toBe(fromEvents[0].errorText);
  });
});

describe('judgeToolOk — one judgement, two surfaces', () => {
  it('fails on any of the three orthogonal signals, independently', () => {
    // Orthogonal results report independently (CLAUDE.md 防御模式): a call can
    // be any one of these without the other two, and each alone means failure.
    expect(judgeToolOk({ result: { ok: true }, errorCode: null })).toBe(true);
    expect(judgeToolOk({ result: { ok: false }, errorCode: null })).toBe(false);
    expect(judgeToolOk({ result: { timed_out: true }, errorCode: null })).toBe(false);
    expect(judgeToolOk({ result: { ok: true }, errorCode: 'denied' })).toBe(false);
  });

  it('treats an absent or unparseable result as a success', () => {
    // The runner only records a tool_call for a call it actually executed, so
    // "cannot read the detail" must not become "the call failed".
    expect(judgeToolOk({ result: null, errorCode: null })).toBe(true);
    expect(judgeToolOk({ result: null, errorCode: 'tool_error' })).toBe(false);
  });

  it('derives timed_out itself so the two callers cannot drift apart', () => {
    expect(toolTimedOut({ error: 'timeout', timed_out: true })).toBe(true);
    expect(toolTimedOut({ ok: true })).toBe(false);
    expect(toolTimedOut(null)).toBe(false);
  });
});
