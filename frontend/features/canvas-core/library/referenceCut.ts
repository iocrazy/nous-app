// features/canvas-core/library/referenceCut.ts
//
// "Which of this asset's files will the provider actually receive?" — asked
// BEFORE the asset is on the board, which is the whole point of the hover
// preview (spec §1 row 7: today the answer only appears after a run).
//
// ⚠️ This is NOT a copy of `AssetNodeView`'s greying rule and must not become
// one. That one ranks among the card's CHECKED files, because the bundle
// endpoint is handed `selected_file_ids` and trims within them. Here there is
// no card and therefore no selection, so it ranks among every file a freshly
// placed card would reference — which is `primarySlotFileIds`' population seen
// through `orderedReferenceFiles`' order. Same question, two different cards.
//
// The ORDER is `orderedReferenceFiles`, the mirror of the backend's
// `_slot_priority`. That is what makes "the cut is the tail" true rather than
// a guess: get the order wrong and the table dims a file that was sent.

import type { AssetFileRow, AssetType } from '../../../services/assetsService';
import { orderedReferenceFiles } from '../smart/assetFiles';

export interface ReferenceRow {
  resourceId: string;
  slot: string;
  /** true = the provider will receive it; false = trimmed at the ceiling. */
  sends: boolean;
}

export function referenceCut(
  files: readonly AssetFileRow[],
  assetType: AssetType,
  loadoutId: string | null,
  maxRefs: number | null,
): ReferenceRow[] {
  const ordered = orderedReferenceFiles(files, assetType, loadoutId);
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
