// frontend/components/assets/assetSlots.ts
//
// MIRROR OF `backend/app/services/assets/slots.py` (PRIMARY_SLOT / SLOTS /
// UNSORTED). Slots are code constants on both sides on purpose — users cannot
// define slots in v1 (spec decision 15) — which means this file is a SECOND
// copy of a table whose authority lives in Python. Edit that file first, then
// re-copy here; `assetSlots.test.ts` hardcodes the same values a third time so
// a one-sided edit fails a test instead of shipping as a 400 `invalid_slot`.

export const ASSET_TYPES = [
  'character',
  'location',
  'prop',
  'costume',
  'prompt',
  'audio',
] as const;

export type AssetType = (typeof ASSET_TYPES)[number];

/**
 * type → primary slot, the one that drives readiness.
 * `prompt` is null: it has no file slot, its body IS the primary.
 */
export const PRIMARY_SLOT: Record<AssetType, string | null> = {
  character: 'sheet',
  location: 'establishing',
  prop: 'turnaround',
  costume: 'flat',
  prompt: null,
  audio: 'primary',
};

/** type → every named slot, primary first. `unsorted` is implicit for all. */
export const SLOTS: Record<AssetType, readonly string[]> = {
  character: ['sheet', 'stills', 'expressions', 'extras', 'worn'],
  location: ['establishing', 'keyframes', 'details', 'layout'],
  prop: ['turnaround', 'in_scene', 'details'],
  costume: ['flat', 'worn', 'details'],
  prompt: ['examples'],
  audio: ['primary', 'variants'],
};

/** The implicit catch-all slot every type accepts. */
export const UNSORTED = 'unsorted';

/**
 * Every slot a picker may offer for `type`, in render order: the named slots
 * followed by `unsorted`. Returns a fresh array — the tables above are module
 * state, and a caller sorting or splicing the result must not edit them.
 */
export function slotsFor(type: AssetType): string[] {
  return [...SLOTS[type], UNSORTED];
}

// ─── Link rules ─────────────────────────────────────────────────────────────
//
// MIRROR of `slots.py`'s `LINK_RULES` / `_AUDIO_SUBTYPE_FOR_RELATION` /
// `link_allowed`, added for the entity sheet's relation sections (P2 Task 7).
//
// This half IS pinned, same as the slot tables above:
// `backend/tests/services/assets/test_slots_frontend_mirror.py` parses
// LINK_RULES / AUDIO_SUBTYPE_FOR_RELATION / LINK_RELATIONS out of this file and
// compares them against `slots.py`. A one-sided edit fails the backend suite.
// (`assetSlots.test.ts` hardcodes the same pairs a third time and therefore
// stays green on drift — it is not the pin.)
//
// What it buys: the "Add" dialog searches only the type the relation can
// accept, so an impossible pair is unreachable in the UI rather than a 422
// `link_not_allowed` after the user picked something.

/** How one asset relates to another. Same union as `assetsService`'s
 *  `AssetLinkRelation`; declared here because this is the table's home and
 *  importing the service from a constants module would invert the dependency. */
export type AssetLinkRelation = 'wears' | 'holds' | 'ambience_of' | 'voice_of';

export const LINK_RELATIONS = [
  'wears',
  'holds',
  'ambience_of',
  'voice_of',
] as const satisfies readonly AssetLinkRelation[];

/** relation → [from type, to type]. */
export const LINK_RULES: Record<AssetLinkRelation, readonly [AssetType, AssetType]> = {
  wears: ['character', 'costume'],
  holds: ['character', 'prop'],
  ambience_of: ['audio', 'location'],
  voice_of: ['audio', 'character'],
};

/**
 * The audio subtypes each audio relation accepts. A relation absent from this
 * table has no subtype condition — the backend applies the check only to the
 * two audio relations, and mirroring "no entry means no constraint" keeps the
 * two sides answering the same question.
 */
export const AUDIO_SUBTYPE_FOR_RELATION: Partial<
  Record<AssetLinkRelation, readonly string[]>
> = {
  ambience_of: ['sfx', 'music'],
  voice_of: ['voice'],
};

/**
 * Mirror of `link_allowed`. `fromSubtype` null is read as `""` — same as
 * Python's `(from_subtype or "")` — so an audio asset with no subtype set
 * fails the check rather than passing it by accident.
 *
 * NO PRODUCTION CALLER YET — the sheet's link picker narrows by
 * `LINK_RULES[relation][1]`, which is the same rule read from the front. This
 * function is the MIRROR'S foothold: it is the piece that has to match
 * `link_allowed` gate for gate, and `assetSlots.test.ts` is the only thing
 * standing between a Python-side edit and a 422 the UI cannot see coming
 * (the backend pin parses the slot tables only).
 */
export function linkAllowed(
  relation: AssetLinkRelation,
  fromType: AssetType,
  fromSubtype: string | null,
  toType: AssetType,
): boolean {
  const rule = LINK_RULES[relation];
  if (!rule || rule[0] !== fromType || rule[1] !== toType) return false;
  const allowedSub = AUDIO_SUBTYPE_FOR_RELATION[relation];
  if (allowedSub && !allowedSub.includes(fromSubtype ?? '')) return false;
  return true;
}
