/**
 * Pins the frontend slot mirror against the backend table.
 *
 * The expected values below are HARDCODED on purpose — they are a second,
 * independent copy of `backend/app/services/assets/slots.py`. If someone edits
 * that file and re-copies it into `assetSlots.ts` without touching this test,
 * this test goes red and the drift is caught here rather than as a 400
 * `invalid_slot` in production.
 */

import { describe, expect, it } from 'vitest';
import { ASSET_TYPES, PRIMARY_SLOT, SLOTS, UNSORTED, slotsFor } from './assetSlots';

// Verbatim from slots.py (PRIMARY_SLOT). `prompt` has no file slot: its body
// is the primary, so readiness there keys on prompt_positive, not a slot.
const EXPECTED_PRIMARY: Record<string, string | null> = {
  character: 'sheet',
  location: 'establishing',
  prop: 'turnaround',
  costume: 'flat',
  prompt: null,
  audio: 'primary',
};

// Verbatim from slots.py (SLOTS). Order matters: primary first, and the UI
// renders the tabs in this order.
const EXPECTED_SLOTS: Record<string, string[]> = {
  character: ['sheet', 'stills', 'expressions', 'extras', 'worn'],
  location: ['establishing', 'keyframes', 'details', 'layout'],
  prop: ['turnaround', 'in_scene', 'details'],
  costume: ['flat', 'worn', 'details'],
  prompt: ['examples'],
  audio: ['primary', 'variants'],
};

describe('asset slot table', () => {
  it('covers exactly the six asset types', () => {
    expect([...ASSET_TYPES].sort()).toEqual(
      ['audio', 'character', 'costume', 'location', 'prompt', 'prop'].sort(),
    );
    expect(Object.keys(PRIMARY_SLOT).sort()).toEqual([...ASSET_TYPES].sort());
    expect(Object.keys(SLOTS).sort()).toEqual([...ASSET_TYPES].sort());
  });

  it('matches the backend primary slots', () => {
    expect(PRIMARY_SLOT).toEqual(EXPECTED_PRIMARY);
  });

  it('matches the backend slot lists, in order', () => {
    for (const type of ASSET_TYPES) {
      expect(SLOTS[type]).toEqual(EXPECTED_SLOTS[type]);
    }
  });

  it("puts each type's primary slot first in its slot list", () => {
    for (const type of ASSET_TYPES) {
      const primary = PRIMARY_SLOT[type];
      if (primary === null) continue;
      expect(SLOTS[type][0]).toBe(primary);
    }
  });
});

describe('slotsFor', () => {
  it('appends the implicit unsorted slot last', () => {
    expect(slotsFor('character')).toEqual([
      'sheet',
      'stills',
      'expressions',
      'extras',
      'worn',
      'unsorted',
    ]);
    expect(slotsFor('character').at(-1)).toBe('unsorted');
  });

  it('appends unsorted for every type, including prompt', () => {
    for (const type of ASSET_TYPES) {
      expect(slotsFor(type)).toEqual([...EXPECTED_SLOTS[type], UNSORTED]);
    }
  });

  it('returns a fresh array so callers cannot mutate the table', () => {
    const first = slotsFor('prop');
    first.push('bogus');
    expect(slotsFor('prop')).toEqual(['turnaround', 'in_scene', 'details', 'unsorted']);
  });
});
