import { describe, it, expect } from 'vitest';
import { driftExceedsThreshold, formatDrift, parseServerDate } from './clockDrift';

describe('parseServerDate', () => {
  it('parses an RFC 7231 Date header', () => {
    const ms = parseServerDate('Wed, 11 Jun 2026 06:06:41 GMT');
    expect(ms).toBe(Date.UTC(2026, 5, 11, 6, 6, 41));
  });

  it('returns null for garbage / missing headers', () => {
    expect(parseServerDate(null)).toBeNull();
    expect(parseServerDate('not a date')).toBeNull();
  });
});

describe('driftExceedsThreshold', () => {
  const FIVE_MIN = 5 * 60_000;

  it('flags drift beyond the threshold in either direction', () => {
    expect(driftExceedsThreshold(10 * 60_000, FIVE_MIN)).toBe(true);
    expect(driftExceedsThreshold(-22 * 24 * 3_600_000, FIVE_MIN)).toBe(true);
  });

  it('tolerates small offsets (NTP jitter, header 1s resolution)', () => {
    expect(driftExceedsThreshold(30_000, FIVE_MIN)).toBe(false);
    expect(driftExceedsThreshold(-90_000, FIVE_MIN)).toBe(false);
  });
});

describe('formatDrift', () => {
  it('renders human units', () => {
    expect(formatDrift(-22 * 24 * 3_600_000)).toBe('22 days');
    expect(formatDrift(3 * 3_600_000)).toBe('3 hours');
    expect(formatDrift(-7 * 60_000)).toBe('7 minutes');
    expect(formatDrift(45_000)).toBe('1 minute');
  });
});
