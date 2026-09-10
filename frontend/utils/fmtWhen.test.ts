import { describe, expect, it } from 'vitest';

import { fmtWhen } from './fmtWhen';

describe('fmtWhen', () => {
  it('renders a real instant in local time', () => {
    expect(fmtWhen('2026-09-11T01:00:00Z')).toBe(new Date('2026-09-11T01:00:00Z').toLocaleString());
  });
  it('hands back what it was given when that is not a time', () => {
    expect(fmtWhen('soon')).toBe('soon');
  });
  it('has an em dash for nothing at all', () => {
    expect(fmtWhen(null)).toBe('—');
    expect(fmtWhen(undefined)).toBe('—');
  });
});
