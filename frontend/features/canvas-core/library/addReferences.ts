// features/canvas-core/library/addReferences.ts
//
// One library pick → one or more `manual_refs` entries on a prompt node.
//
// Lifted out of `PromptNodeView.addManualRef`, which could only ever handle a
// single resource id. The store-read pattern is preserved verbatim and it is
// load-bearing: the node is re-read from the store AFTER every await, because
// minting is a round trip and a closure's snapshot would drop whatever else
// wrote to the node meanwhile.
//
// Every failure is TYPED and returned. A pick that quietly adds nothing is the
// silent no-op this repo keeps re-learning — the caller renders `failed`.

import { fetchAssetDetail } from '../../../services/assetsService';
import { primarySlotFileIds } from '../smart/assetFiles';
import { importResourceAsCanvasMedia } from '../smart/mediaImport';
import type { GeneratedImageRef, PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { LibraryItem } from './librarySearch';

export type AddReferenceFailure = 'mint_failed' | 'no_image_file' | 'not_an_image';

export interface AddReferencesResult {
  added: number;
  /** Already on the node, deduped by url. */
  skipped: number;
  /** Typed, never silent — one entry per item we could not turn into a ref. */
  failed: Array<{ item: LibraryItem; reason: AddReferenceFailure }>;
}

class ReferenceError_ extends Error {
  reason: AddReferenceFailure;
  constructor(reason: AddReferenceFailure) {
    super(reason);
    this.name = 'ReferenceError';
    this.reason = reason;
  }
}

/**
 * The durable refs one library item contributes.
 *
 * An asset contributes SEVERAL — its primary-slot files — which is why this
 * answers a list rather than a single ref. `/api/v1/resources/{id}/cover` and
 * `/api/v1/generated-media/{id}/file` are the two url families the backend's
 * reference bridge resolves (`DURABLE_PREFIXES`); anything else is dropped
 * server-side and reported as `dropped_refs` after the run, which is far too
 * late to be useful.
 */
export async function resolveReferenceRefs(
  item: LibraryItem,
  scopeId: string,
): Promise<GeneratedImageRef[]> {
  if (item.store === 'generated') {
    if (item.kind === 'video') throw new ReferenceError_('not_an_image');
    return [{ url: `/api/v1/generated-media/${item.id}/file`, kind: 'image' }];
  }
  if (item.store === 'uploads') {
    if (item.kind !== 'image') throw new ReferenceError_('not_an_image');
    const minted = await importResourceAsCanvasMedia(item.id);
    return [{ url: minted.url, kind: minted.kind }];
  }
  // assets — the card's own rule for "which files does this asset reference",
  // reused rather than re-derived (assetFiles.ts owns that question).
  const detail = await fetchAssetDetail(scopeId, item.id);
  const ids = primarySlotFileIds(detail, null);
  if (ids.length === 0) throw new ReferenceError_('no_image_file');
  return ids.map((rid) => ({
    // The RELATIVE form: `manual_refs` is persisted into `nodes_json` and an
    // absolute url would bake this deployment's API host into the document.
    url: `/api/v1/resources/${rid}/cover`,
    kind: 'image' as const,
  }));
}

export async function addReferences(
  nodeId: string,
  items: readonly LibraryItem[],
  scopeId: string,
): Promise<AddReferencesResult> {
  const out: AddReferencesResult = { added: 0, skipped: 0, failed: [] };
  for (const item of items) {
    let resolved: GeneratedImageRef[];
    try {
      resolved = await resolveReferenceRefs(item, scopeId);
    } catch (err) {
      console.error('[addReferences] could not resolve', item, err);
      out.failed.push({
        item,
        reason: err instanceof ReferenceError_ ? err.reason : 'mint_failed',
      });
      continue;
    }
    // The LIVE node, re-read after the await — a stale closure would drop any
    // other write that landed while the round trip was in flight.
    const store = useCanvasCoreStore.getState();
    const current =
      ((store.nodes.find((n) => (n as { id?: unknown }).id === nodeId) as
        | { data?: PromptNodeData }
        | undefined)?.data?.manual_refs ?? []) as GeneratedImageRef[];
    const have = new Set(current.map((r) => r.url));
    const fresh = resolved.filter((r) => !have.has(r.url));
    out.skipped += resolved.length - fresh.length;
    if (fresh.length === 0) continue;
    store.patchNode(nodeId, { data: { manual_refs: [...current, ...fresh] } });
    out.added += fresh.length;
  }
  return out;
}
