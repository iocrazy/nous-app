// features/canvas-core/smart/promptStrip.ts
//
// The prompt node's input-reference strip, IN THE ORDER THE RUN DELIVERS.
//
// The strip is a claim about the request: which pictures go, in what order,
// and which ones the provider's ceiling will trim. `generationRunner` builds
// that order, and it is NOT the order the strip used to draw:
//
//     const sourceUrls = [
//       ...assets.reference_urls,                    // cards, then mentions
//       ...(ctx.source_urls ?? []).filter(…),        // wired images + manual
//     ];
//
// Asset references LEAD, and both ceilings that trim references trim from the
// TAIL. The strip drew wired images first and numbered mentions after them, so
// with `max_refs = 2`, one wired input and two mentions it dimmed the second
// mention while the backend actually dropped the wired input. A badge that
// describes the run confidently and wrongly is worse than no badge — this repo
// has paid for that lesson (P4's own card checklist carries the same warning).
//
// ─ What this can and cannot know ────────────────────────────────────────────
//
// Three populations feed one request, and they are knowable to different
// depths:
//
//   wired images   exactly one reference each. Known.
//   a MENTION      expands to the asset's primary-slot files — several
//                  references behind one thumbnail. Known only because the
//                  picker snapshots the ids into `ref_resource_ids` when the
//                  mention is inserted; a mention saved before that field
//                  existed, or one whose detail fetch failed, is UNKNOWN.
//   an asset CARD  contributes `min(len(selection), max_refs)` references
//                  AHEAD of everything on this strip. Counted here, drawn on
//                  the card itself (which owns its own checklist and its own
//                  greying). Only the COUNT is modelled: which files a card
//                  sends is decided server-side in slot-priority order, not in
//                  the array order this can see.
//
// When a span is unknown, everything after it stops being knowable too. Rather
// than guess, this reports `position: null` / `beyondLimit: false` from that
// point on, and the strip draws no number and dims nothing. Silence is the
// honest answer; a number would be a fabrication about the request.
//
// The post-run ledgers stay authoritative either way — `last_dropped_refs` from
// the backend and `last_mention_dropped` from the bundle both land on the same
// "Ignored" badge.
//
// ─ Why this takes values, not the graph ─────────────────────────────────────
//
// A node view must never subscribe to `s.nodes` (`useGraphDerived.ts`): a drag
// tick replaces that array every frame, so the subscription re-renders the card
// no matter how well it is memoised. So this function is handed three already-
// stable projections — the node's own mentions, the input urls a selector hook
// answers, and a NUMBER for what the upstream cards contribute — and touches no
// store at all. That also makes it a plain pure function to test.

import type { AssetNodeData } from './types';
import type { MentionedAsset } from './mentionedAssets';
import { assetReferenceUrl } from './promptInputs';

/** One tile on the strip. */
export type StripEntry =
  | {
      kind: 'mention';
      asset: MentionedAsset;
      /** 1-based delivery position of its FIRST reference, or null when the
       *  position cannot be known (see the header). */
      position: number | null;
      /** The provider will send NONE of this entry's references. A mention
       *  that straddles the ceiling is not marked: some of it survives. */
      beyondLimit: boolean;
      /** How many references this tile stands for; null when unknown. */
      refCount: number | null;
    }
  | {
      kind: 'input';
      url: string;
      position: number | null;
      beyondLimit: boolean;
      refCount: number;
    };

/**
 * How many references the connected asset CARDS put ahead of this strip.
 *
 * Their bundle applies the provider ceiling WITHIN each card's own selection
 * (`build_bundle::_restrict_to_selection`), which is the same arithmetic the
 * card's own row-dimming uses — so the two surfaces agree by construction.
 *
 * A NUMBER on purpose: it is what a node view subscribes to, and a number
 * compares stable under `Object.is`, so a drag that moves the card without
 * changing its checklist re-renders nothing.
 */
export function upstreamAssetRefCount(
  cards: ReadonlyArray<{ data: AssetNodeData }>,
  maxRefs: number | null,
): number {
  const cap = maxRefs ?? Number.POSITIVE_INFINITY;
  let total = 0;
  for (const card of cards) {
    total += Math.min((card.data.selected_file_ids ?? []).length, cap);
  }
  return total;
}

export interface PromptStripInput {
  /** This prompt's own `mentioned_assets`, in body order. */
  mentions: readonly MentionedAsset[];
  /** The durable images wired in plus `manual_refs`, in delivery order. */
  inputUrls: readonly string[];
  /** {@link upstreamAssetRefCount} for the cards feeding this prompt. */
  assetRefsAhead: number;
  /** The provider's reference ceiling; null = unknown, which dims nothing. */
  maxRefs: number | null;
}

/**
 * The strip, in delivery order: mentions (asset references) first, then the
 * wired images and manual refs.
 *
 * Connected asset CARDS are not drawn here — they are their own cards — but
 * their references are counted through `assetRefsAhead`, because they occupy
 * the first positions of the same request and therefore decide where
 * everything on this strip lands.
 */
export function promptStripEntries({
  mentions,
  inputUrls,
  assetRefsAhead,
  maxRefs,
}: PromptStripInput): StripEntry[] {
  let next = assetRefsAhead + 1;

  // url -> the position it was first given. Deduped because the runner dedupes
  // (`resolveAssetInputs` on the asset side, the `.filter()` above on the wired
  // side): the same picture sent twice is one reference, and counting it twice
  // would push everything after it off by one.
  const seen = new Map<string, number>();
  const take = (url: string): number => {
    const found = seen.get(url);
    if (found !== undefined) return found;
    seen.set(url, next);
    return next++;
  };

  const entries: StripEntry[] = [];
  /** Set once a span cannot be measured; from then on nothing is asserted. */
  let unknown = false;

  for (const asset of mentions) {
    const ids = asset.ref_resource_ids;
    if (!Array.isArray(ids)) {
      // Not "zero references" — "we did not ask". A mention saved before the
      // snapshot existed, or one whose detail fetch failed at insert.
      entries.push({ kind: 'mention', asset, position: null, beyondLimit: false, refCount: null });
      unknown = true;
      continue;
    }
    if (unknown) {
      entries.push({ kind: 'mention', asset, position: null, beyondLimit: false, refCount: null });
      continue;
    }
    const positions = ids.map((id) => take(assetReferenceUrl(id)));
    if (positions.length === 0) {
      // A real answer: this asset contributes no reference images. A `prompt`
      // asset has no primary FILE slot — its body is the contribution. Dimming
      // it would say the provider dropped something that was never sent.
      entries.push({ kind: 'mention', asset, position: null, beyondLimit: false, refCount: 0 });
      continue;
    }
    entries.push({
      kind: 'mention',
      asset,
      position: positions[0],
      beyondLimit: maxRefs !== null && positions.every((p) => p > maxRefs),
      refCount: positions.length,
    });
  }

  for (const url of inputUrls) {
    if (unknown) {
      entries.push({ kind: 'input', url, position: null, beyondLimit: false, refCount: 1 });
      continue;
    }
    const position = take(url);
    entries.push({
      kind: 'input',
      url,
      position,
      beyondLimit: maxRefs !== null && position > maxRefs,
      refCount: 1,
    });
  }

  return entries;
}
