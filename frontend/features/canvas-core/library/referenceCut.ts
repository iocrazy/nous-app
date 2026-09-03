// features/canvas-core/library/referenceCut.ts
//
// "Which of this asset's files will the provider actually receive?" — asked
// BEFORE the asset is on the board, which is the whole point of the hover
// preview (spec §1 row 7: today the answer only appears after a run).
//
// THE POPULATION IS THE PRIMARY SLOT, because that is what both paths a pick
// can take actually send:
//
//   - Add as reference — `addReferences.ts` mints one ref per
//     `primarySlotFileIds(detail, null)`.
//   - Place on canvas — `factories.ts::assetNodeData` seeds
//     `selected_file_ids: primarySlotFileIds(asset, loadoutId)`, and the bundle
//     endpoint trims within that.
//
// So this asks `primarySlotFileIds` the same question with the same argument,
// rather than deriving a second answer. Ranking every attached file instead
// would have the card promise references that no pick sends: with a character
// carrying `sheet ×2 + stills + worn`, a pick delivers the two `sheet` files
// while an all-files table says `worn` Sends and `stills` is Cut. Both false.
//
// ⚠️ The remaining difference from `AssetNodeView`'s greying rule is smaller
// than it looks, and worth stating so neither drifts: that one ranks among the
// card's CHECKED files, because a placed card lets the user tick files off and
// the bundle endpoint is handed the survivors. Here nothing has been placed,
// so there is no selection yet — the population is the one a freshly placed
// card would START with, which is the same function's output. Same question,
// two different cards.
//
// The ORDER is `orderedReferenceFiles`, the mirror of the backend's
// `_slot_priority`. That is what makes "the cut is the tail" true rather than
// a guess. For today's single-slot population it reduces to `sort_order`, but
// it stays the authority on rank the day a type's primary slot is not one
// slot — and it is what dedupes a resource attached twice.

import { orderedReferenceFiles, primarySlotFileIds, type AssetNodeSeed } from '../smart/assetFiles';

export interface ReferenceRow {
  resourceId: string;
  slot: string;
  /** true = the provider will receive it; false = trimmed at the ceiling. */
  sends: boolean;
}

export function referenceCut(
  asset: AssetNodeSeed,
  loadoutId: string | null,
  maxRefs: number | null,
): ReferenceRow[] {
  const delivered = new Set(primarySlotFileIds(asset, loadoutId));
  const ordered = orderedReferenceFiles(asset.files ?? [], asset.asset_type, loadoutId).filter(
    (f) => delivered.has(f.resource_id),
  );
  return ordered.map((f, i) => ({
    resourceId: f.resource_id,
    slot: f.slot,
    // `null` is UNKNOWN — still loading, fetch failed, model absent from the
    // map — and every consumer of `useModelCapabilities` renders FULL support
    // on it. Reading it as 0 would tell the user their references are being
    // thrown away on the day the capabilities endpoint hiccups.
    sends: maxRefs === null ? true : i < maxRefs,
  }));
}

/**
 * How many of the asset's OTHER files a pick leaves behind — everything not in
 * the delivered set, counted by RESOURCE (a resource attached to two slots,
 * one of them primary, is delivered and is not counted here).
 *
 * The table above would otherwise be silently shorter than the asset: a
 * character with eight files shows two rows and nothing says where the other
 * six went.
 *
 * ⚠️ A file pinned to a loadout that is not bound lands in this count too,
 * though it is not literally "in another slot" — it is genuinely not sent by
 * default, which is what the footer is telling the user, but the wording is
 * approximate for that one case. Both real callers pass `loadoutId === null`,
 * so a loadout-pinned primary-slot file does reach it.
 */
export function otherSlotCount(asset: AssetNodeSeed, loadoutId: string | null): number {
  const delivered = new Set(primarySlotFileIds(asset, loadoutId));
  const rest = new Set<string>();
  for (const f of asset.files ?? []) {
    if (!delivered.has(f.resource_id)) rest.add(f.resource_id);
  }
  return rest.size;
}
