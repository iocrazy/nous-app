import { describe, it, expect } from 'vitest';
import { formatElapsed } from './formatElapsed';

describe('formatElapsed', () => {
  it('shows seconds under a minute', () => {
    expect(formatElapsed(0)).toBe('0s');
    expect(formatElapsed(5)).toBe('5s');
    expect(formatElapsed(59)).toBe('59s');
  });

  it('shows minutes and seconds under an hour', () => {
    expect(formatElapsed(60)).toBe('1m 0s');
    expect(formatElapsed(125)).toBe('2m 5s');
    expect(formatElapsed(3599)).toBe('59m 59s');
  });

  it('shows hours and minutes past an hour', () => {
    expect(formatElapsed(3600)).toBe('1h 0m');
    expect(formatElapsed(3661)).toBe('1h 1m');
    expect(formatElapsed(7325)).toBe('2h 2m');
  });
});
