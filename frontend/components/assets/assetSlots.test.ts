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
import {
  ASSET_TYPES,
  AUDIO_SUBTYPE_FOR_RELATION,
  LINK_RELATIONS,
  LINK_RULES,
  PRIMARY_SLOT,
  SLOTS,
  UNSORTED,
  linkAllowed,
  slotsFor,
} from './assetSlots';

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

// ─── Link rules ─────────────────────────────────────────────────────────────
//
// The same hardcoded-second-copy trick as above, for `LINK_RULES` /
// `link_allowed`. This half of the mirror is NOT covered by the backend's
// `test_slots_frontend_mirror.py` (it parses only the slot tables), so this
// file is the ONLY thing standing between a Python-side edit and a relation
// picker offering a pair the server refuses with 422 `link_not_allowed`.

// Verbatim from slots.py (LINK_RULES).
const EXPECTED_LINK_RULES: Record<string, [string, string]> = {
  wears: ['character', 'costume'],
  holds: ['character', 'prop'],
  ambience_of: ['audio', 'location'],
  voice_of: ['audio', 'character'],
};

// Verbatim from slots.py (_AUDIO_SUBTYPE_FOR_RELATION).
const EXPECTED_AUDIO_SUBTYPES: Record<string, string[]> = {
  ambience_of: ['sfx', 'music'],
  voice_of: ['voice'],
};

describe('LINK_RULES mirrors slots.py', () => {
  it('carries exactly the four relations, with the same endpoints', () => {
    expect(Object.fromEntries(Object.entries(LINK_RULES).map(([k, v]) => [k, [...v]]))).toEqual(
      EXPECTED_LINK_RULES,
    );
  });

  it('LINK_RELATIONS enumerates the table keys', () => {
    expect([...LINK_RELATIONS].sort()).toEqual(Object.keys(EXPECTED_LINK_RULES).sort());
  });

  it('only the two audio relations carry a subtype condition', () => {
    expect(
      Object.fromEntries(
        Object.entries(AUDIO_SUBTYPE_FOR_RELATION).map(([k, v]) => [k, [...(v ?? [])]]),
      ),
    ).toEqual(EXPECTED_AUDIO_SUBTYPES);
  });
});

describe('linkAllowed mirrors link_allowed', () => {
  it('accepts each rule\'s own pair', () => {
    expect(linkAllowed('wears', 'character', null, 'costume')).toBe(true);
    expect(linkAllowed('holds', 'character', null, 'prop')).toBe(true);
    expect(linkAllowed('ambience_of', 'audio', 'sfx', 'location')).toBe(true);
    expect(linkAllowed('ambience_of', 'audio', 'music', 'location')).toBe(true);
    expect(linkAllowed('voice_of', 'audio', 'voice', 'character')).toBe(true);
  });

  it('rejects a swapped direction', () => {
    // costume → character is not "wears" backwards; it is nothing.
    expect(linkAllowed('wears', 'costume', null, 'character')).toBe(false);
  });

  it('rejects the wrong target type', () => {
    expect(linkAllowed('wears', 'character', null, 'prop')).toBe(false);
    expect(linkAllowed('ambience_of', 'audio', 'sfx', 'character')).toBe(false);
  });

  it('an audio asset with no subtype is refused, not waved through', () => {
    // Python reads `(from_subtype or "")`, so null is a value that fails the
    // membership test rather than a missing condition that skips it.
    expect(linkAllowed('voice_of', 'audio', null, 'character')).toBe(false);
    expect(linkAllowed('ambience_of', 'audio', '', 'location')).toBe(false);
    expect(linkAllowed('voice_of', 'audio', 'sfx', 'character')).toBe(false);
  });
});
