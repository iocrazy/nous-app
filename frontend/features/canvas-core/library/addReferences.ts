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
//
// The ceiling is enforced HERE and counted in REFS, not picks: one asset can
// resolve to several refs, so a caller that slices its own pick list cannot
// bound what actually lands. `maxRefs` is checked against the live node before
// every patch, and the tail it refuses is reported as `clamped`.

import { fetchAssetDetail } from '../../../services/assetsService';
import { primarySlotFileIds } from '../smart/assetFiles';
import { importResourceAsCanvasMedia } from '../smart/mediaImport';
import type { GeneratedImageRef, PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { LibraryItem } from './librarySearch';

export type AddReferenceFailure = 'mint_failed' | 'no_image_file' | 'not_an_image';

export interface ResolveRefsOptions {
  /**
   * Let a VIDEO through instead of refusing it.
   *
   * OFF by default, and the default is the load-bearing half: a reference is
   * an image, always — `addReferences` never passes this. `placeLibraryItems`
   * does, because a media CARD can hold a video. The gate widens by exactly
   * one kind: audio, doc and pdf uploads stay a typed `not_an_image` refusal,
   * since a media card renders none of them.
   */
  allowVideo?: boolean;
}

export interface AddReferencesOptions {
  /**
   * Ceiling on the node's TOTAL `manual_refs`, checked against the live node
   * before each patch. Omitted means no ceiling — which is what the three-arg
   * callers get.
   */
  maxRefs?: number;
}

export interface AddReferencesResult {
  added: number;
  /** Already on the node, deduped by url. */
  skipped: number;
  /** Resolved fine, but did not fit under `maxRefs`. Never silent. */
  clamped: number;
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
 * `opts.allowVideo` is the ONE axis a caller may widen — see
 * {@link ResolveRefsOptions}. Everything else about the resolution is the
 * same for a reference and for a card, which is why there is one resolver.
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
  opts?: ResolveRefsOptions,
): Promise<GeneratedImageRef[]> {
  const videoOk = opts?.allowVideo === true;
  if (item.store === 'generated') {
    if (item.kind === 'video') {
      if (!videoOk) throw new ReferenceError_('not_an_image');
      return [{ url: `/api/v1/generated-media/${item.id}/file`, kind: 'video' }];
    }
    return [{ url: `/api/v1/generated-media/${item.id}/file`, kind: 'image' }];
  }
  if (item.store === 'uploads') {
    if (item.kind !== 'image' && !(videoOk && item.kind === 'video')) {
      throw new ReferenceError_('not_an_image');
    }
    // The kind comes back from the MINT, not from the list row: the row says
    // what the library thinks it is, the mint says what was actually stored.
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
  opts?: AddReferencesOptions,
): Promise<AddReferencesResult> {
  const out: AddReferencesResult = { added: 0, skipped: 0, clamped: 0, failed: [] };
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
    // Room is measured against the LIVE node and recomputed per item, and it
    // is measured AFTER the dedupe — so an already-referenced pick occupies no
    // slot and cannot push a legitimate one out.
    let take = fresh;
    if (typeof opts?.maxRefs === 'number') {
      const room = Math.max(0, opts.maxRefs - current.length);
      take = fresh.slice(0, room);
      out.clamped += fresh.length - take.length;
    }
    // `continue`, not `break`: a later item may still be a duplicate or a
    // failure, and the caller's message is only honest if every item is
    // accounted for.
    if (take.length === 0) continue;
    store.patchNode(nodeId, { data: { manual_refs: [...current, ...take] } });
    out.added += take.length;
  }
  return out;
}
