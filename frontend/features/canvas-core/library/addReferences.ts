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
 * The typed reason behind a `resolveReferenceRefs` refusal.
 *
 * Anything the resolver did not raise itself — a network failure inside the
 * mint, a 500 from the detail fetch — is a `mint_failed`, because that is what
 * the user needs to be told about it: the round trip did not come back.
 *
 * Exported so a SECOND consumer of the resolver (`mentionLibraryItems`) reads
 * the reason the same way rather than duck-typing `.reason` off an error class
 * it cannot see.
 */
export function referenceFailureReason(err: unknown): AddReferenceFailure {
  return err instanceof ReferenceError_ ? err.reason : 'mint_failed';
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

/** Slots left on the LIVE node under a ceiling. Read fresh on every call —
 *  the node is re-read after every await for the same reason. */
function roomOn(nodeId: string, maxRefs: number): number {
  const current =
    ((useCanvasCoreStore.getState().nodes.find((n) => (n as { id?: unknown }).id === nodeId) as
      | { data?: PromptNodeData }
      | undefined)?.data?.manual_refs ?? []) as GeneratedImageRef[];
  return Math.max(0, maxRefs - current.length);
}

export async function addReferences(
  nodeId: string,
  items: readonly LibraryItem[],
  scopeId: string,
  opts?: AddReferencesOptions,
): Promise<AddReferencesResult> {
  const out: AddReferencesResult = { added: 0, skipped: 0, clamped: 0, failed: [] };
  for (const [i, item] of items.entries()) {
    // ROOM FIRST, resolve second — the order is the fix, not a tidy-up.
    // Resolving an UPLOAD calls `/generated-media/import-from-resource`, which
    // has no idempotency key and registers a fresh `generated_media` row every
    // time. Resolving before measuring meant ten uploads picked into two free
    // slots minted ten rows, kept two, and left eight `user_upload` rows in
    // the user's Generated inbox to triage. Multi-select turned that from a
    // one-at-a-time wart into a per-gesture one.
    //
    // With no room left there is nothing further to learn from a round trip,
    // so the tail is counted as clamped WITHOUT resolving and the loop stops.
    // ⚠️ The count is one per remaining ITEM: an asset's ref count is only
    // knowable from its detail fetch, which is exactly the round trip being
    // avoided. So `clamped` can UNDERSTATE for assets — it never overstates,
    // and it is never zero when something was refused.
    if (typeof opts?.maxRefs === 'number' && roomOn(nodeId, opts.maxRefs) === 0) {
      out.clamped += items.length - i;
      break;
    }
    let resolved: GeneratedImageRef[];
    try {
      resolved = await resolveReferenceRefs(item, scopeId);
    } catch (err) {
      console.error('[addReferences] could not resolve', item, err);
      out.failed.push({ item, reason: referenceFailureReason(err) });
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
    // `continue`, not `break`: this item contributed nothing (it was already
    // on the node), but the node may still have room and a later item may
    // still be a duplicate or a failure. The only `break` is the one at the
    // top, where there is no room left for anything.
    if (take.length === 0) continue;
    store.patchNode(nodeId, { data: { manual_refs: [...current, ...take] } });
    out.added += take.length;
  }
  return out;
}
