// features/canvas-core/library/placeLibraryItems.ts
//
// Library rows → canvas nodes. One function behind the panel's "Place on
// Canvas" button AND the empty-pane drop (Task 8), because they are the same
// operation asked two ways.
//
// EVERY outcome says something. Placed nothing because it was all already
// here, placed nothing because the mint failed, and placed nothing because
// there was nothing selected are three different answers; a caller that gets
// one number cannot tell them apart, which is the silent no-op this repo keeps
// re-learning.
//
// KNOWN LIMIT: media rows go through `resolveReferenceRefs`, which is the
// REFERENCE resolver — it refuses a video with `not_an_image`. So placing a
// generated video reports a typed failure rather than a video card. That is
// loud rather than silent, and the fix belongs in the resolver (whose url
// templates would otherwise get a second copy here), not in this file.

import { fetchAssetDetail, type AssetRow } from '../../../services/assetsService';
import { layoutAssetLanes, buildProjectAssetNodes } from '../smart/assetPlacement';
import { createAssetNode, createMediaNode } from '../smart/factories';
import type { AssetNodeSeed } from '../smart/assetFiles';
import { SMART_NODE_DEFAULT_WIDTH } from '../smart/types';
import type { CanvasNode } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { resolveReferenceRefs, type AddReferenceFailure } from './addReferences';
import type { LibraryItem } from './librarySearch';

export interface PlaceResult {
  nodeIds: string[];
  inserted: number;
  /** Asset already on this board — placing it twice would feed one asset's
   *  references into the graph from two cards. */
  skipped: number;
  failed: Array<{ item: LibraryItem; reason: AddReferenceFailure }>;
}

/** Asset ids already referenced by a card here. Placing one twice would feed
 *  the same asset's references into the graph from two places. */
function assetIdsOnBoard(nodes: readonly CanvasNode[]): Set<string> {
  const out = new Set<string>();
  for (const n of nodes) {
    const node = n as { type?: string; data?: { asset_id?: unknown } };
    if (node.type === 'asset' && typeof node.data?.asset_id === 'string') {
      out.add(node.data.asset_id);
    }
  }
  return out;
}

export async function placeLibraryItems(
  items: readonly LibraryItem[],
  scopeId: string,
  position: { x: number; y: number } | null,
): Promise<PlaceResult> {
  const out: PlaceResult = { nodeIds: [], inserted: 0, skipped: 0, failed: [] };
  if (items.length === 0) return out;

  const present = assetIdsOnBoard(useCanvasCoreStore.getState().nodes);
  const assetItems = items.filter((i) => i.store === 'assets');
  const mediaItems = items.filter((i) => i.store !== 'assets');

  // ── Assets: the DETAIL row, never the list row. `file_counts_by_slot` is a
  // tally, so a card seeded from a summary references nothing.
  const details: AssetRow[] = [];
  for (const item of assetItems) {
    if (present.has(item.id)) {
      out.skipped += 1;
      continue;
    }
    try {
      details.push(await fetchAssetDetail(scopeId, item.id));
    } catch (err) {
      console.error('[placeLibraryItems] asset detail failed:', err);
      out.failed.push({ item, reason: 'mint_failed' });
    }
  }

  // ── Uploads / generations: durable urls, minted the same way a reference is.
  const refs: Array<{ url: string; kind: 'image' | 'video' }> = [];
  for (const item of mediaItems) {
    try {
      const resolved = await resolveReferenceRefs(item, scopeId);
      for (const r of resolved) refs.push({ url: r.url, kind: r.kind === 'video' ? 'video' : 'image' });
    } catch (err) {
      console.error('[placeLibraryItems] could not resolve', item, err);
      out.failed.push({
        item,
        reason: (err as { reason?: AddReferenceFailure }).reason ?? 'mint_failed',
      });
    }
  }

  const store = useCanvasCoreStore.getState();
  // The LIVE list, re-read after the awaits above.
  const existing = store.nodes;
  const fresh: CanvasNode[] = [];

  if (details.length > 0) {
    if (position) {
      for (const slot of layoutAssetLanes(details, position)) {
        fresh.push(createAssetNode(slot.item as AssetNodeSeed, { position: slot.position }) as CanvasNode);
      }
    } else {
      // No drop point: this is the Project Assets gesture, whose layout rule
      // (lanes starting at the next free position) already exists.
      fresh.push(...buildProjectAssetNodes(details, existing).nodes);
    }
    out.inserted += details.length;
  }

  if (refs.length > 0) {
    const width = SMART_NODE_DEFAULT_WIDTH.media;
    const origin = position ?? { x: 0, y: 0 };
    fresh.push(
      createMediaNode(
        {
          // The upload card's own naming rule: plural images are a Group.
          title: refs.length > 1 ? 'Group' : refs[0].kind === 'video' ? 'Video' : 'Image',
          items: refs,
        },
        {
          position: {
            x: Math.round(origin.x - width / 2),
            y: Math.round(origin.y - 60),
          },
        },
      ) as CanvasNode,
    );
    out.inserted += refs.length;
  }

  if (fresh.length === 0) return out;

  // ONE write. Two would make a half-placed board observable, and each would
  // push its own history entry for what the user did once.
  store.setNodes([...existing, ...fresh]);
  out.nodeIds = fresh.map((n) => String((n as { id?: unknown }).id));
  store.setSelection(out.nodeIds);
  return out;
}
