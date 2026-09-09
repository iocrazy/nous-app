/**
 * The exactly-once property lives here.
 *
 * A7's named failure mode is "a tool call renders twice, or vanishes". Both
 * surfaces normalise through this module, so pinning it here pins it for both
 * without mounting either.
 */

import { describe, expect, it } from 'vitest';

import type { AgentRunEvent, ChatToolCall } from '../../types';
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
