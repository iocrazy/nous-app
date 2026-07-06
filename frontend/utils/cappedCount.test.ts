import { describe, expect, it } from 'vitest';
import { COUNT_CAP, formatCappedCount } from './cappedCount';

describe('formatCappedCount', () => {
  it('renders exact counts up to the cap', () => {
    expect(formatCappedCount(0)).toBe('0');
    expect(formatCappedCount(841)).toBe('841');
    expect(formatCappedCount(COUNT_CAP)).toBe('100,000');
  });

  it('renders the capped sentinel (server counts at most cap+1) as 100,000+', () => {
    expect(formatCappedCount(COUNT_CAP + 1)).toBe('100,000+');
  });
});
