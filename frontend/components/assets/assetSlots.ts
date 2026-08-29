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
