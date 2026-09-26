/**
 * What the mascot should be doing, derived from the chat panel's state.
 *
 * `running`  — a turn is streaming (专注创作: pinned ears, Agent ring).
 * `waiting`  — the newest assistant turn parked on a typed question that
 *              nobody has answered yet (思考: one ear twitches, a "?").
 * `idle`     — everything else.
 *
 * Streaming wins over a stale question: while the answer is being sent the
 * old question is on its way out.
 */
export type FabActivity = 'idle' | 'running' | 'waiting';

export function deriveFabActivity(input: {
  sending: boolean;
  lastAssistantAwaitingInput: boolean;
}): FabActivity {
  if (input.sending) return 'running';
  if (input.lastAssistantAwaitingInput) return 'waiting';
  return 'idle';
}
