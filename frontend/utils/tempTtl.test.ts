import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ttlBadgeText } from './tempTtl';

describe('ttlBadgeText', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-26T12:00:00Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "never expires" when ttlDays is null', () => {
    expect(ttlBadgeText('2026-05-20T00:00:00Z', null)).toBe('never expires');
  });

  it('returns "expires in N days" for future expiry', () => {
    // Created today at midnight, 30-day TTL → expires in 30 days.
    expect(ttlBadgeText('2026-05-26T00:00:00Z', 30)).toMatch(/expires in 3[01] days/);
  });

  it('returns "expired" for past expiry', () => {
    // Created 60 days ago, 30-day TTL → expired.
    expect(ttlBadgeText('2026-03-25T00:00:00Z', 30)).toBe('expired');
  });

  it('returns "expires today" when remaining ≤ 1 day', () => {
    // Created ~22h ago, 1-day TTL → ~2h remaining → expires today.
    expect(ttlBadgeText('2026-05-25T14:00:00Z', 1)).toBe('expires today');
  });

  it('returns empty string for unparseable createdAt', () => {
    expect(ttlBadgeText('not a date', 30)).toBe('');
  });
});
