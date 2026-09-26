import { describe, expect, it } from 'vitest';

import { deriveFabActivity, endsWithAssistantReply } from './fabActivity';

describe('deriveFabActivity', () => {
  it('is running while a turn is streaming, whatever the last message says', () => {
    expect(deriveFabActivity({ sending: true, lastAssistantAwaitingInput: true })).toBe('running');
    expect(deriveFabActivity({ sending: true, lastAssistantAwaitingInput: false })).toBe('running');
  });

  it('is waiting when the newest assistant turn parked on an unanswered question', () => {
    expect(deriveFabActivity({ sending: false, lastAssistantAwaitingInput: true })).toBe('waiting');
  });

  it('is idle otherwise', () => {
    expect(deriveFabActivity({ sending: false, lastAssistantAwaitingInput: false })).toBe('idle');
  });
});

describe('endsWithAssistantReply', () => {
  it('is true when the settled history ends on an assistant message', () => {
    expect(endsWithAssistantReply([{ role: 'user' }, { role: 'assistant' }])).toBe(true);
  });

  it('is false when the turn produced nothing after the user message', () => {
    expect(endsWithAssistantReply([{ role: 'assistant' }, { role: 'user' }])).toBe(false);
  });

  it('is false for an empty or failed reload', () => {
    expect(endsWithAssistantReply([])).toBe(false);
    expect(endsWithAssistantReply(null)).toBe(false);
  });
});
