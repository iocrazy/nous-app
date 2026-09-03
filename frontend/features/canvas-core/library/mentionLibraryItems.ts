// features/canvas-core/library/mentionLibraryItems.ts
//
// `⌥` + drop on a prompt node → CHIPS in the body, one per dropped item.
//
// Spec §3.3 names the two chip kinds ("asset → `@` chip 走 bundle；图片 → 图片
// chip") and §4.4 makes "正文追加 chip" an acceptance item. The first cut wrote
// plain text instead — `insertText('@' + title + ' ')` per item — and that was
// wrong twice over:
//
//   1. It delivered NOTHING. A run reads image chips (`promptImageRefs`) and
//      asset chips (`mentionedAssets`); the characters `@Harbour` are prose.
//      The drop hint said "Insert as Mention" and the pipeline never saw one.
//   2. It DESTROYED text. `insertText` routes through the editor's
//      `insertAtMention`, which deletes from the last `@` in the 80 characters
//      before the caret up to the caret — correct for the `@` picker, which
//      must consume the pending query, and catastrophic for a drop, which has
//      no pending query. Item two ate item one's freshly written `@Harbour `,
//      so a five-item drop left ONE mention; a body carrying an email address
//      before the caret lost everything from the `@` onward.
//
// Chips are immune to (1) and to the multi-item half of (2): a chip is a leaf
// node, so the next insert's 80-character scan reads it as a leaf separator
// rather than as an `@`. The single remaining `insertAtMention` behaviour —
// the FIRST insert consuming a literal `@` the user typed earlier — is the
// editor handle's contract, shared with the `@` picker, and is not this
// module's to change.
//
// Every refusal is TYPED and returned, and the caller says it out loud. This
// path is a user action that triggers work, so a silent no-op is the defect
// this repo keeps re-learning (CLAUDE.md: 用户动作→agent 触发的每条路径必须返回
// 类型化结果).

import { fetchAssetDetail, type AssetType } from '../../../services/assetsService';
import { primarySlotFileIds } from '../smart/assetFiles';
import type { MentionedAsset } from '../smart/mentionedAssets';
import type { PromptImageRef } from '../smart/nodes/promptImageRefs';
import {
  referenceFailureReason,
  resolveReferenceRefs,
  type AddReferenceFailure,
} from './addReferences';
import type { LibraryItem } from './librarySearch';

/** Exactly the two inserters this needs, so the helper can be pinned without
 *  an editor. `PromptBodyEditorHandle` satisfies it structurally. */
export interface MentionInserters {
  insertImage: (image: PromptImageRef) => void;
  insertAsset: (asset: MentionedAsset) => void;
}

export interface MentionLibraryResult {
  /** Chips actually written into the document. An asset contributes one; an
   *  image item contributes one per durable ref it resolved to. */
  mentioned: number;
  /** Typed, never silent — one entry per item that produced no chip. */
  failed: Array<{ item: LibraryItem; reason: AddReferenceFailure }>;
}

export async function mentionLibraryItems(
  items: readonly LibraryItem[],
  scopeId: string,
  handle: MentionInserters,
): Promise<MentionLibraryResult> {
  const out: MentionLibraryResult = { mentioned: 0, failed: [] };
  // SEQUENTIAL on purpose: the chips land in the order they are inserted, and
  // that order is the order the user selected them in. Racing the round trips
  // would shuffle the body.
  for (const item of items) {
    if (item.store === 'assets') {
      // The detail carries the two fields a `LibraryItem` cannot: the cover
      // (for the chip's face) and the primary-slot file ids (for the input
      // strip's span). A failure does NOT block the mention — that is the `@`
      // picker's rule at `PromptNodeView.handleMentionAsset`, and the chip is
      // fully functional without either: the RUN re-fetches the bundle from
      // `asset_id`, which is the authority. Logged, then degraded.
      let cover: string | null = null;
      let refIds: string[] | undefined;
      try {
        const detail = await fetchAssetDetail(scopeId, item.id);
        cover = detail.cover_file_id;
        refIds = primarySlotFileIds(detail, null);
      } catch (err) {
        console.error('[mentionLibraryItems] asset detail fetch failed:', err);
      }
      handle.insertAsset({
        asset_id: item.id,
        name: item.title,
        // The library row's `kind` IS `asset_type` (`assetToLibraryItem`).
        asset_type: item.kind as AssetType,
        cover_file_id: cover,
        // ABSENT means NOT ASKED — the strip renders that differently from an
        // empty list, so a failed fetch must not become `[]` here.
        ...(refIds ? { ref_resource_ids: refIds } : {}),
      });
      out.mentioned += 1;
      continue;
    }
    // uploads / generated — an image chip IS a reference, so it resolves
    // through the very same durable-url resolver the reference path uses.
    // `allowVideo` stays off: a prompt body renders image chips only, and a
    // video upload is a typed `not_an_image` refusal rather than a chip that
    // shows nothing.
    let refs;
    try {
      refs = await resolveReferenceRefs(item, scopeId);
    } catch (err) {
      console.error('[mentionLibraryItems] could not resolve', item, err);
      out.failed.push({ item, reason: referenceFailureReason(err) });
      continue;
    }
    for (const ref of refs) {
      handle.insertImage({ url: ref.url, alias: item.title, kind: ref.kind });
      out.mentioned += 1;
    }
  }
  return out;
}
