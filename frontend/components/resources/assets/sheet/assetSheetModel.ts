// frontend/components/resources/assets/sheet/assetSheetModel.ts
//
// The entity sheet's arithmetic, with no React in it.
//
// Everything here answers a question the sheet would otherwise answer inline
// inside JSX, where it could only be tested by rendering a page: which pins
// the Board shows and in what order, what a loadout switch does to the `worn`
// pin, which relation sections a type has, and what "Copy loadout prompt"
// puts on the clipboard. Drag-and-drop in particular is only testable this
// way — JSDOM has no real pointer stream, so the ORDER function is the thing
// tests can falsify, not the gesture that calls it.

import {
  AUDIO_SUBTYPE_FOR_RELATION,
  LINK_RULES,
  PRIMARY_SLOT,
  SLOTS,
  UNSORTED,
  type AssetLinkRelation,
  type AssetType,
} from '../../../assets/assetSlots';
import type {
  AssetFileRow,
  AssetLinkRow,
  AssetLoadoutRow,
  AssetRow,
  AssetRowDetail,
} from '../../../../services/assetsService';

// ─── Board ──────────────────────────────────────────────────────────────────

/** One tile on the board: a slot and the files attached to it. */
export interface BoardPin {
  slot: string;
  files: AssetFileRow[];
  /** `files.length`, named so a caller reading only the badge cannot mistake
   *  a filtered view for the slot's total. */
  count: number;
}

/**
 * The user's saved board order, read out of `attrs.board_layout.slot_order`.
 *
 * Returns `[]` for anything that is not an array of strings. `attrs` is a free
 * jsonb column: a value written by an older build, by hand, or by another
 * client can be any shape at all, and a board that throws on a malformed
 * layout would make the asset unopenable rather than merely unsorted.
 */
export function savedSlotOrder(attrs: Record<string, unknown> | null | undefined): string[] {
  const layout = (attrs ?? {})['board_layout'];
  if (!layout || typeof layout !== 'object') return [];
  const order = (layout as Record<string, unknown>)['slot_order'];
  if (!Array.isArray(order)) return [];
  return order.filter((slot): slot is string => typeof slot === 'string');
}

/**
 * The secondary slots, in render order: the saved order first (entries the
 * type no longer has are dropped), then any slot the save predates, in slot
 * table order.
 *
 * The primary slot is NOT here — it is the big frame above the grid. `unsorted`
 * IS, because a file can be attached to it and a pin that exists but is never
 * rendered is a file the user cannot find.
 *
 * Appending unknown slots rather than ignoring them is the load-bearing half:
 * a layout saved before a slot was added must not hide the new slot forever.
 */
export function boardSlotOrder(
  assetType: AssetType,
  attrs: Record<string, unknown> | null | undefined,
): string[] {
  const primary = PRIMARY_SLOT[assetType];
  const secondary = [...(SLOTS[assetType] ?? []), UNSORTED].filter((s) => s !== primary);
  const allowed = new Set(secondary);
  const saved = savedSlotOrder(attrs).filter((slot) => allowed.has(slot));
  const seen = new Set(saved);
  return [...saved, ...secondary.filter((slot) => !seen.has(slot))];
}

/**
 * Move one slot from `from` to `to`, returning a NEW array.
 *
 * Out-of-range indices return the input order unchanged rather than throwing
 * or silently appending: a drop outside the grid is "nothing happened", and
 * that has to be representable.
 */
export function moveSlot(order: readonly string[], from: number, to: number): string[] {
  if (from === to) return [...order];
  if (from < 0 || from >= order.length || to < 0 || to >= order.length) return [...order];
  const next = [...order];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/**
 * Files of one slot, newest-user-order first (`sort_order`, then
 * `resource_id` so the order is total and a re-render cannot reshuffle ties).
 *
 * `loadoutId` filters the loadout-scoped slots: a file carrying a
 * `loadout_id` belongs to THAT outfit and is hidden while another is
 * selected; a file with `loadout_id === null` belongs to no outfit and shows
 * under every one — it is imagery of the character, not of an outfit.
 * Passing `loadoutId === null` (no loadout selected) shows everything.
 */
export function filesForSlot(
  files: readonly AssetFileRow[],
  slot: string,
  loadoutId: string | null,
): AssetFileRow[] {
  return files
    .filter((file) => file.slot === slot)
    .filter((file) => loadoutId === null || file.loadout_id === null || file.loadout_id === loadoutId)
    .sort(
      (a, b) => a.sort_order - b.sort_order || a.resource_id.localeCompare(b.resource_id),
    );
}

/** The big frame's file, or null when the primary slot is empty (or the type
 *  has no primary slot at all — `prompt`, whose body is its primary). */
export function primaryFile(
  detail: AssetRowDetail,
  loadoutId: string | null,
): AssetFileRow | null {
  const primary = PRIMARY_SLOT[detail.asset_type];
  if (primary === null) return null;
  return filesForSlot(detail.files, primary, loadoutId)[0] ?? null;
}

/** Every secondary pin, in board order, including the empty ones — an empty
 *  slot is drawn dashed so the board shows the SHAPE of a complete asset of
 *  this type, not only what happens to exist. */
export function boardPins(detail: AssetRowDetail, loadoutId: string | null): BoardPin[] {
  return boardSlotOrder(detail.asset_type, detail.attrs).map((slot) => {
    const files = filesForSlot(detail.files, slot, loadoutId);
    return { slot, files, count: files.length };
  });
}

// ─── Loadouts ───────────────────────────────────────────────────────────────

/** The loadout that opens by default: the one flagged `is_default`, else the
 *  first by `sort_order`. Null when the asset has none. */
export function defaultLoadoutId(loadouts: readonly AssetLoadoutRow[]): string | null {
  if (loadouts.length === 0) return null;
  const flagged = loadouts.find((l) => l.is_default);
  if (flagged) return flagged.id;
  return [...loadouts].sort((a, b) => a.sort_order - b.sort_order)[0].id;
}

/** Loadouts belong to characters only (spec §3.4). */
export function hasLoadouts(assetType: AssetType): boolean {
  return assetType === 'character';
}

/**
 * The `loadout_id` a NEW file attached to `slot` should carry.
 *
 * `worn` is the one slot whose contents belong to an OUTFIT rather than to the
 * asset: a still of the character in the night robe is a fact about that
 * loadout. Every other slot holds imagery of the asset itself, and stamping
 * the selected loadout onto it would make the file the user just attached
 * disappear the moment they switched outfits — with nothing on screen saying
 * where it went.
 *
 * The reverse of `filesForSlot`'s reading rule (a null `loadout_id` shows
 * under every loadout), and it is what both `EquipDialog` and the
 * attach-from-inbox path in `GenerateMissingDialog` send.
 */
export function loadoutForSlot(slot: string, selectedLoadoutId: string | null): string | null {
  return slot === 'worn' ? selectedLoadoutId : null;
}

// ─── Relations ──────────────────────────────────────────────────────────────

/**
 * One relation block on the sheet.
 *
 * `direction` is the difference between "this character wears these costumes"
 * (outgoing, editable here) and "these characters wear this costume"
 * (incoming — the row lives on the other asset, so this side can list it but
 * must not offer to remove it).
 */
/**
 * Every section heading the sheet can render, enumerated so the i18n parity
 * test can iterate them. `relationSectionsFor` only ever returns these.
 */
export const RELATION_SECTION_KEYS = [
  'wears',
  'holds',
  'attachedTo',
  'wornBy',
  'heldBy',
] as const;

export type RelationSectionKey = (typeof RELATION_SECTION_KEYS)[number];

export interface RelationSectionSpec {
  /** i18n suffix under `assets.rel.*`, and the section's test id. */
  key: RelationSectionKey;
  direction: 'outgoing' | 'incoming';
  /** Which link rows belong in this section. */
  relations: AssetLinkRelation[];
  /**
   * The type the Add dialog searches, or null when the section cannot be
   * added to from here: an incoming section, or an audio asset whose
   * `subtype` does not satisfy any relation's condition. The second case is
   * a REFUSAL WITH A REASON — see `addBlockedReason`.
   */
  targetType: AssetType | null;
  /** The relation a new link would use. Null whenever `targetType` is. */
  addRelation: AssetLinkRelation | null;
  /** i18n suffix under `assets.rel.blocked.*` when adding is impossible for a
   *  reason the user can act on. Null when adding works, or when the section
   *  is simply incoming (which needs no explanation). */
  addBlockedReason: 'audioSubtype' | null;
}

/**
 * Which relation sections a sheet shows.
 *
 * `location` and `prompt` get none. A location IS pointed at by `ambience_of`
 * links, but the sheet does not list them: v1 shows the audio side of that
 * pair only, and inventing a second surface for the same row would give two
 * places to keep in step. Recorded rather than silently omitted.
 */
export function relationSectionsFor(
  assetType: AssetType,
  subtype: string | null,
): RelationSectionSpec[] {
  if (assetType === 'character') {
    return [
      {
        key: 'wears',
        direction: 'outgoing',
        relations: ['wears'],
        targetType: LINK_RULES.wears[1],
        addRelation: 'wears',
        addBlockedReason: null,
      },
      {
        key: 'holds',
        direction: 'outgoing',
        relations: ['holds'],
        targetType: LINK_RULES.holds[1],
        addRelation: 'holds',
        addBlockedReason: null,
      },
    ];
  }
  if (assetType === 'costume') {
    return [
      {
        key: 'wornBy',
        direction: 'incoming',
        relations: ['wears'],
        targetType: null,
        addRelation: null,
        addBlockedReason: null,
      },
    ];
  }
  if (assetType === 'prop') {
    return [
      {
        key: 'heldBy',
        direction: 'incoming',
        relations: ['holds'],
        targetType: null,
        addRelation: null,
        addBlockedReason: null,
      },
    ];
  }
  if (assetType === 'audio') {
    // One section, two possible relations — which one is live depends on the
    // audio subtype, exactly as `link_allowed` decides it server-side. An
    // audio asset with no subtype can still SHOW its links; it just cannot
    // gain one, and the section says why rather than failing at 422 time.
    const relation = audioRelationFor(subtype);
    return [
      {
        key: 'attachedTo',
        direction: 'outgoing',
        relations: ['ambience_of', 'voice_of'],
        targetType: relation ? LINK_RULES[relation][1] : null,
        addRelation: relation,
        addBlockedReason: relation ? null : 'audioSubtype',
      },
    ];
  }
  return [];
}

/** The one relation an audio asset of this subtype may create, or null. */
export function audioRelationFor(subtype: string | null): AssetLinkRelation | null {
  const value = subtype ?? '';
  for (const [relation, subtypes] of Object.entries(AUDIO_SUBTYPE_FOR_RELATION)) {
    if ((subtypes ?? []).includes(value)) return relation as AssetLinkRelation;
  }
  return null;
}

/** The rows a section renders, and which asset id each one points AT. */
export function linkRowsFor(
  detail: AssetRowDetail,
  spec: RelationSectionSpec,
): { link: AssetLinkRow; otherId: string }[] {
  const source = spec.direction === 'outgoing' ? detail.links : detail.linked_by;
  return source
    .filter((link) => spec.relations.includes(link.relation))
    .map((link) => ({
      link,
      otherId: spec.direction === 'outgoing' ? link.to_asset_id : link.from_asset_id,
    }));
}

/** Every asset id this sheet has to resolve a name for. */
export function relatedAssetIds(detail: AssetRowDetail): string[] {
  const ids = new Set<string>();
  for (const link of detail.links) ids.add(link.to_asset_id);
  for (const link of detail.linked_by) ids.add(link.from_asset_id);
  for (const loadout of detail.loadouts) {
    for (const id of loadout.costume_ids) ids.add(id);
    for (const id of loadout.prop_ids) ids.add(id);
  }
  return [...ids];
}

// ─── Copy loadout prompt ────────────────────────────────────────────────────

/**
 * The prompt a user pastes into another tool, assembled client-side in the
 * order the delivery protocol uses server-side (spec §6.3):
 *
 *   asset.prompt_positive → loadout.prompt_extra → each costume's positive →
 *   each prop's positive
 *
 * Blank parts are dropped, not rendered as empty lines: a costume with no
 * prompt contributes nothing, and a separator with nothing around it would
 * read as a missing piece.
 *
 * `related` maps asset id → row. An id with no entry contributes nothing —
 * a costume whose row could not be fetched must not become the literal string
 * "undefined" in the middle of a prompt the user is about to paste.
 */
export function composeLoadoutPrompt(
  asset: AssetRow,
  loadout: AssetLoadoutRow | null,
  related: Record<string, AssetRow | undefined>,
): string {
  const parts: string[] = [];
  const push = (value: string | null | undefined) => {
    const text = (value ?? '').trim();
    if (text !== '') parts.push(text);
  };

  push(asset.prompt_positive);
  push(loadout?.prompt_extra);
  for (const id of loadout?.costume_ids ?? []) push(related[id]?.prompt_positive);
  for (const id of loadout?.prop_ids ?? []) push(related[id]?.prompt_positive);

  return parts.join(', ');
}

/**
 * The negatives the same set contributes, de-duplicated, in first-seen order
 * (spec 6.3: "negative 取并集去重").
 *
 * NO PRODUCTION CALLER YET - it is P4's foothold: the canvas delivery protocol
 * composes the positive and the negative together, and this half is written and
 * pinned now so that task inherits the rule rather than re-deriving it.
 */
export function composeLoadoutNegative(
  asset: AssetRow,
  loadout: AssetLoadoutRow | null,
  related: Record<string, AssetRow | undefined>,
): string {
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (value: string | null | undefined) => {
    for (const piece of (value ?? '').split(',')) {
      const text = piece.trim();
      if (text === '' || seen.has(text)) continue;
      seen.add(text);
      out.push(text);
    }
  };
  add(asset.prompt_negative);
  for (const id of loadout?.costume_ids ?? []) add(related[id]?.prompt_negative);
  for (const id of loadout?.prop_ids ?? []) add(related[id]?.prompt_negative);
  return out.join(', ');
}

// ─── Header extras ──────────────────────────────────────────────────────────

/** `attrs.loopable`, read defensively — jsonb, so anything could be in there. */
export function audioLoopable(attrs: Record<string, unknown> | null | undefined): boolean {
  return (attrs ?? {})['loopable'] === true;
}

/**
 * `attrs.duration_sec` as a number, or null.
 *
 * Accepts a numeric string too: the column is jsonb and both shapes have been
 * written by hand. A value that is neither is null rather than `NaN`, which
 * would render as the literal "NaN s".
 */
export function audioDurationSec(
  attrs: Record<string, unknown> | null | undefined,
): number | null {
  const raw = (attrs ?? {})['duration_sec'];
  const value = typeof raw === 'string' ? Number(raw) : raw;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  return value;
}

/** `m:ss`. Seconds are floored — a track is not 3:60. */
export function formatDuration(seconds: number): string {
  const total = Math.floor(seconds);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

/**
 * The placeholders a prompt asset declares (`attrs.placeholders`).
 *
 * Accepts the two shapes seeded prompts use — a list of names, or an object
 * of name → description — and normalizes both to pairs. Anything else is an
 * empty list: a malformed value shows no panel rather than a panel of
 * "[object Object]".
 */
export function promptPlaceholders(
  attrs: Record<string, unknown> | null | undefined,
): { name: string; hint: string }[] {
  const raw = (attrs ?? {})['placeholders'];
  if (Array.isArray(raw)) {
    return raw
      .filter((name): name is string => typeof name === 'string')
      .map((name) => ({ name, hint: '' }));
  }
  if (raw && typeof raw === 'object') {
    return Object.entries(raw as Record<string, unknown>).map(([name, hint]) => ({
      name,
      hint: typeof hint === 'string' ? hint : '',
    }));
  }
  return [];
}

/**
 * One row of the `platform_params` editor.
 *
 * `original` is what the column actually holds, kept alongside the editable
 * text so an untouched row can be written back with its TYPE intact. Without
 * it, rendering `{"model": "4"}` as the text `4` and parsing it back on the way
 * out rewrites the column to `{"model": 4}` - a silent retype of a value the
 * user never touched. `null` marks a row the user added, which has no stored
 * value to preserve.
 */
export interface PlatformParamRow {
  key: string;
  /** What the input shows. */
  value: string;
  original: { key: string; value: unknown } | null;
}

/** How a stored value is rendered into its input. Strings appear as themselves
 *  (quoting them would make every model name look like JSON); everything else
 *  as JSON, so a nested object is visible rather than `[object Object]`. */
export function platformParamText(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** `platform_params` flattened to editor rows, each carrying its stored value. */
export function platformParamRows(
  params: Record<string, unknown> | null | undefined,
): PlatformParamRow[] {
  return Object.entries(params ?? {}).map(([key, value]) => ({
    key,
    value: platformParamText(value),
    original: { key, value },
  }));
}

/**
 * Editor rows back into the column.
 *
 * A row whose key and text still match what was stored is written back AS
 * STORED - the string `"4"` stays the string `"4"`. Only a row the user
 * actually changed is re-read from its text, and then only JSON that parses
 * becomes a non-string: `1:1` stays text, `{"steps": 30}` becomes an object.
 * Blank keys are dropped (a half-typed new row is not a parameter yet).
 */
export function platformParamsFromRows(rows: readonly PlatformParamRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const row of rows) {
    const name = row.key.trim();
    if (name === '') continue;
    const untouched =
      row.original !== null &&
      name === row.original.key &&
      row.value === platformParamText(row.original.value);
    if (untouched) {
      out[name] = row.original.value;
      continue;
    }
    try {
      out[name] = JSON.parse(row.value);
    } catch {
      // Not JSON - keep the literal text. This is the common case (`1:1`, a
      // model slug) and is not an error worth reporting.
      out[name] = row.value;
    }
  }
  return out;
}

/**
 * Structural equality, used to decide whether a param edit is a REQUEST.
 *
 * Blurring through a field the user only looked at must not PATCH the column
 * and must not bump `updated_at` - the same rule the inline text fields follow.
 * Written out rather than done with `JSON.stringify` because that compares key
 * ORDER too, and would report a reordered-but-identical object as a change.
 */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, index) => deepEqual(item, b[index]));
  }
  if (typeof a !== 'object') return false;
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every(
    (key) => Object.prototype.hasOwnProperty.call(right, key) && deepEqual(left[key], right[key]),
  );
}

// ─── Canvas ─────────────────────────────────────────────────────────────────

/**
 * The canvas kind an "Open in canvas" would create for this asset type.
 *
 * Four of the six types have a canvas kind of their own; the rest (`prompt`,
 * `audio`) open a plain smart canvas. Returning `'smart'` for those rather
 * than refusing is deliberate: the board is useful either way, and there is
 * no kind to invent.
 */
export function canvasKindFor(
  assetType: AssetType,
): 'character' | 'location' | 'prop' | 'costume' | 'smart' {
  if (
    assetType === 'character' ||
    assetType === 'location' ||
    assetType === 'prop' ||
    // 'costume' was legal in the DB CHECK (mig 446) but missing from both
    // enums, so this branch used to fall through to 'smart' — the canvas got
    // created, just under the wrong kind. P4 ruling G restored it.
    assetType === 'costume'
  ) {
    return assetType;
  }
  return 'smart';
}
