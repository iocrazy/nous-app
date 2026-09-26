import { describe, expect, it } from 'vitest';

import { deriveFabActivity } from './fabActivity';

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
