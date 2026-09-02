/**
 * Which files an asset CARD references — the one place that question is
 * answered (P4 Task 4, fix round 1).
 *
 * It used to be answered twice: the factory seeded `selected_file_ids` from
 * one predicate and the view listed checkboxes from another. They disagreed
 * in exactly one case — a loadout-pinned file in the primary slot with no
 * loadout bound, which is what BOTH entry points produce, since neither
 * passes a `loadoutId`. The factory selected it, the view refused to draw a
 * row for it, so the card shipped a reference the user could not see or
 * uncheck, and Task 5 would have handed the wrong outfit to the model with
 * nothing on screen saying why. Seeding and rendering now share a predicate
 * and a source file, which makes that class of divergence unrepresentable
 * rather than merely fixed.
 *
 * ⚠️ This is NOT `assetSheetModel.ts::filesForSlot`, and it must not be
 * replaced by it. That function short-circuits on `loadoutId === null` and
 * returns everything, because on the SHEET a null loadout means "this slot
 * has no loadout dimension" (`examples`, the audio slots) — show it all. On
 * a canvas card a null loadout means something else: "this character,
 * generically, no outfit chosen". Pre-selecting an outfit-specific file
 * there is precisely the wrong-costume bug. Same-shaped rule, different
 * question; the two are kept apart on purpose.
 */

import {
  PRIMARY_SLOT,
  referenceSlotPriority,
} from '../../../components/assets/assetSlots';
import type {
  AssetFileRow,
  AssetRow,
  AssetType,
} from '../../../services/assetsService';

/**
 * The row shape `createAssetNode` needs.
 *
 * `AssetRow` alone cannot seed `selected_file_ids`: it carries
 * `file_counts_by_slot` (a tally) but not the file rows themselves, which
 * only `GET /assets/{id}` returns. So the type is an `AssetRow` widened with
 * the optional `files` — `AssetRowDetail` is assignable as-is, and a caller
 * holding only a summary row still gets a valid node, just with an empty
 * selection.
 */
export type AssetNodeSeed = AssetRow & { files?: readonly AssetFileRow[] };

/**
 * May this file be referenced while `loadoutId` is bound?
 *
 * A file carrying a `loadout_id` belongs to THAT outfit alone. A file with
 * `loadout_id === null` belongs to no outfit and is usable under every one —
 * it is imagery of the character, not of a costume. With no loadout bound,
 * only the second kind qualifies.
 */
export function fileVisibleUnderLoadout(
  file: AssetFileRow,
  loadoutId: string | null,
): boolean {
  return file.loadout_id === null || file.loadout_id === loadoutId;
}

/**
 * The files a freshly placed card starts out referencing: the PRIMARY slot's,
 * in the library's own `sort_order`.
 *
 * The primary slot is the one readiness is derived from, so it is the slot a
 * user means by "this asset" before they say otherwise. `prompt` has no
 * primary FILE slot (`PRIMARY_SLOT.prompt === null` — its body IS the
 * primary), so a prompt asset seeds an empty selection rather than falling
 * back to some other slot's files.
 */
export function primarySlotFileIds(
  asset: AssetNodeSeed,
  loadoutId: string | null,
): string[] {
  const primary = PRIMARY_SLOT[asset.asset_type];
  if (primary === null || primary === undefined) return [];
  return (asset.files ?? [])
    .filter((f) => f.slot === primary && fileVisibleUnderLoadout(f, loadoutId))
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((f) => f.resource_id);
}

/**
 * The rows the card's checklist draws, IN THE ORDER THE BUNDLE WILL SEND THEM:
 * primary → `worn` → `stills` → the type's remaining slots → `unsorted`, then
 * anything in a slot no table names (alphabetically, so it is deterministic).
 *
 * That is `referenceSlotPriority`, the mirror of the backend's
 * `_slot_priority`, and NOT `slotsFor` — which is what this used to walk. The
 * two differ for `character`, the type this whole feature centres on:
 * declaration order puts `worn` LAST, delivery order puts it second. Drawing
 * the list in one order while the provider's `max_refs` trims the tail of the
 * other made the card dim a file that WAS sent and leave un-dimmed the one that
 * was dropped — then the post-run badge on the same card said the opposite.
 * Rendering in delivery order makes the ceiling legible instead: what the
 * provider drops is exactly what is at the bottom.
 *
 * ONE ROW PER RESOURCE. `asset_files` is keyed `(asset_id, resource_id,
 * slot)`, so the same resource can be attached to two slots — but the
 * selection vocabulary is RESOURCES (`selected_file_ids` holds
 * `resource_id`s, which is what the bundle endpoint answers in), so those two
 * attachments are one choice. Drawing both would render two checkboxes that
 * tick and untick together, which reads as a bug; the survivor is the one in
 * the highest-priority slot, which is also the label the user sees.
 *
 * Exported for its test — the ordering, the loadout filter and the dedupe are
 * the parts worth pinning, not the markup around them.
 */
export function orderedReferenceFiles(
  files: readonly AssetFileRow[],
  assetType: AssetType,
  loadoutId: string | null,
): AssetFileRow[] {
  const order = referenceSlotPriority(assetType);
  // An unnamed slot (a file left over from a renamed one) still gets its turn,
  // last and in a deterministic order — the same tail `reference_order` appends
  // with `sorted(set(files_by_slot) - set(order))`. Dropping it, or leaving its
  // position to whatever order the rows arrived in, would be a reference the
  // user attached and cannot see the fate of.
  const rank = (slot: string): number => {
    const i = order.indexOf(slot);
    return i === -1 ? order.length : i;
  };
  const sorted = files
    .filter((f) => fileVisibleUnderLoadout(f, loadoutId))
    .slice()
    .sort(
      (a, b) =>
        rank(a.slot) - rank(b.slot) ||
        (rank(a.slot) === order.length ? a.slot.localeCompare(b.slot) : 0) ||
        a.sort_order - b.sort_order,
    );
  const seen = new Set<string>();
  return sorted.filter((f) => {
    if (seen.has(f.resource_id)) return false;
    seen.add(f.resource_id);
    return true;
  });
}
