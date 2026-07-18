import { describe, expect, it } from 'vitest';
import { monthsWithNotes, stepYear, visibleYearSlots, WHEEL_SLOTS } from './yearWheel';

describe('visibleYearSlots', () => {
  it('returns 5 slots, older above and newer below (ascending)', () => {
    expect(visibleYearSlots(2020, 2030)).toEqual([2018, 2019, 2020, 2021, 2022]);
    expect(visibleYearSlots(2020, 2030)).toHaveLength(WHEEL_SLOTS);
  });

  it('caps the future: years later than nowYear become null', () => {
    // Centered on the current year: the two lower (future) slots are blank.
    expect(visibleYearSlots(2026, 2026)).toEqual([2024, 2025, 2026, null, null]);
  });

  it('caps a single future slot when one year from now', () => {
    expect(visibleYearSlots(2025, 2026)).toEqual([2023, 2024, 2025, 2026, null]);
  });

  it('renders all past years without capping', () => {
    expect(visibleYearSlots(2000, 2026)).toEqual([1998, 1999, 2000, 2001, 2002]);
  });
});

describe('stepYear', () => {
  it('moves into the past without a lower bound (dir -1)', () => {
    expect(stepYear(2020, -1, 2026)).toBe(2019);
    expect(stepYear(1500, -1, 2026)).toBe(1499);
  });

  it('moves toward now (dir +1) but never past nowYear', () => {
    expect(stepYear(2024, 1, 2026)).toBe(2025);
    expect(stepYear(2026, 1, 2026)).toBe(2026); // capped
    expect(stepYear(2030, 1, 2026)).toBe(2026); // already beyond, clamped down
  });
});

describe('monthsWithNotes', () => {
  it('collects distinct month indices for rows with notes', () => {
    const set = monthsWithNotes([
      { day: '2026-01-05', cnt: 2 },
      { day: '2026-01-19', cnt: 1 },
      { day: '2026-07-07', cnt: 3 },
    ]);
    expect([...set].sort((a, b) => a - b)).toEqual([0, 6]);
  });

  it('ignores rows with no notes and malformed days', () => {
    const set = monthsWithNotes([
      { day: '2026-03-01', cnt: 0 },
      { day: 'bad', cnt: 5 },
      { day: '2026-12-31', cnt: 1 },
    ]);
    expect([...set]).toEqual([11]);
  });

  it('returns an empty set for an empty input', () => {
    expect(monthsWithNotes([]).size).toBe(0);
  });
});
