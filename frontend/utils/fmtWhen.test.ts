import { describe, expect, it } from 'vitest';

import { fmtWhen, fmtWhenCompact } from './fmtWhen';

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

describe('fmtWhenCompact', () => {
  // The clock convention stays the READER's (12h or 24h, per locale) — that
  // is the whole point of rendering in local time. What is asserted is the
  // property the row needs: a date, the minute, no seconds, and shorter than
  // the full form.
  it('keeps the date and the minute but drops the seconds', () => {
    const iso = '2026-09-11T01:23:45Z';
    const out = fmtWhenCompact(iso);
    const d = new Date(iso);
    expect(out).not.toMatch(/:\d\d:\d\d/);
    expect(out).toContain(String(d.getDate()));
    expect(out).toContain(String(d.getMinutes()).padStart(2, '0'));
    expect(out.length).toBeLessThan(fmtWhen(iso).length);
  });
  it('keeps the year when it is not the current one', () => {
    const y = new Date().getFullYear();
    const next = `${y + 1}-03-04T05:06:00Z`;
    expect(fmtWhenCompact(next)).toContain(String(y + 1));
    // …and drops it for this year, which is the whole point of the format
    const thisYear = new Date(`${y}-03-04T05:06:00Z`);
    expect(fmtWhenCompact(thisYear.toISOString())).not.toContain(String(y));
  });
  it('keeps the same contract for things that are not instants', () => {
    expect(fmtWhenCompact('soon')).toBe('soon');
    expect(fmtWhenCompact(null)).toBe('—');
  });
});
