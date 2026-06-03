import { describe, it, expect } from 'vitest';
import { shouldOfferUpdate } from './versionCompare';

describe('shouldOfferUpdate', () => {
  it('true when live version differs from running version', () => {
    expect(shouldOfferUpdate('0.23.14', '0.23.15')).toBe(true);
  });
  it('false when versions match', () => {
    expect(shouldOfferUpdate('0.23.15', '0.23.15')).toBe(false);
  });
  it('false when latest is missing (fetch failed / dev)', () => {
    expect(shouldOfferUpdate('0.23.15', null)).toBe(false);
    expect(shouldOfferUpdate('0.23.15', undefined)).toBe(false);
    expect(shouldOfferUpdate('0.23.15', '')).toBe(false);
  });
  it('false when current is unknown', () => {
    expect(shouldOfferUpdate(undefined, '0.23.15')).toBe(false);
  });
  it('tolerates surrounding whitespace', () => {
    expect(shouldOfferUpdate('0.23.15', ' 0.23.15 ')).toBe(false);
  });
});
