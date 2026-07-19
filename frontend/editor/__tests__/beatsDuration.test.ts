/** Beats M3 — target-length parse/format helpers (pure). */
import { describe, expect, it } from 'vitest';

import { DURATION_PRESETS, formatTargetLength, parseDurationInput } from '../beats/beatsDuration';

describe('formatTargetLength', () => {
  it('uses the film apostrophe for whole minutes', () => {
    expect(formatTargetLength(2700)).toBe('45\'');
    expect(formatTargetLength(60)).toBe('1\'');
  });
  it('shows minutes+seconds with a zero-padded seconds field', () => {
    expect(formatTargetLength(90)).toBe('1\'30');
    expect(formatTargetLength(605)).toBe('10\'05');
  });
  it('reads sub-minute lengths in seconds', () => {
    expect(formatTargetLength(45)).toBe('45s');
  });
});

describe('parseDurationInput', () => {
  it('parses whole seconds', () => {
    expect(parseDurationInput('2700')).toBe(2700);
    expect(parseDurationInput('  90 ')).toBe(90);
  });
  it('parses an m:ss clock', () => {
    expect(parseDurationInput('45:00')).toBe(2700);
    expect(parseDurationInput('1:30')).toBe(90);
  });
  it('rejects empty / malformed / out-of-range input', () => {
    expect(parseDurationInput('')).toBeNull();
    expect(parseDurationInput('abc')).toBeNull();
    expect(parseDurationInput('1:60')).toBeNull(); // seconds field > 59
    expect(parseDurationInput('1:2:3')).toBeNull();
    expect(parseDurationInput('0')).toBeNull(); // below the 1s floor
    expect(parseDurationInput('2.5')).toBeNull(); // non-integer
  });
  it('ships the issue preset ladder', () => {
    expect([...DURATION_PRESETS]).toEqual([60, 180, 1200, 2700, 5400]);
  });
});
