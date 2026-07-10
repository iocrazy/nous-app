// Regression for the go-live catch: a date-only string must render the same
// calendar day in every machine time zone. Run under TZ=America/Los_Angeles
// (see package script note) the old new Date('YYYY-MM-DD') path showed 07/09.
import { describe, expect, it, vi } from 'vitest';

vi.mock('../i18n', () => ({ default: { language: 'en' } }));

import { formatDateOnlyShort } from './formatDate';

describe('formatDateOnlyShort', () => {
  it('renders the calendar day from the string, TZ-independent', () => {
    expect(formatDateOnlyShort('2026-07-10')).toContain('10');
    expect(formatDateOnlyShort('2026-07-10')).toContain('Jul');
  });
  it('empty/invalid falls back safely', () => {
    expect(formatDateOnlyShort('')).toBe('');
    expect(formatDateOnlyShort(null)).toBe('');
  });
});
