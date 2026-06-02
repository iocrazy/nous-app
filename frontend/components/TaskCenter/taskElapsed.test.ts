import { describe, it, expect } from 'vitest';
import { formatElapsed } from './taskElapsed';

describe('formatElapsed', () => {
  it('shows whole seconds under a minute', () => {
    expect(formatElapsed(0)).toBe('0s');
    expect(formatElapsed(950)).toBe('0s');
    expect(formatElapsed(1000)).toBe('1s');
    expect(formatElapsed(59_000)).toBe('59s');
  });

  it('shows minutes and seconds under an hour', () => {
    expect(formatElapsed(60_000)).toBe('1m 0s');
    expect(formatElapsed(75_000)).toBe('1m 15s');
    expect(formatElapsed(3_599_000)).toBe('59m 59s');
  });

  it('shows hours and minutes past an hour', () => {
    expect(formatElapsed(3_600_000)).toBe('1h 0m');
    expect(formatElapsed(3_900_000)).toBe('1h 5m');
  });

  it('clamps negative input to 0s (clock skew)', () => {
    expect(formatElapsed(-500)).toBe('0s');
  });
});
