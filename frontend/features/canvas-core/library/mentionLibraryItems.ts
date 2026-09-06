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
// Chips fix (1) outright. (2) is fixed by passing `{ consumeMention: false }`
// on every insert from this path: the deletion is the `@` picker's contract,
// not a universal one, and a drop has no pending query for it to consume.
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
import { EditorGoneError } from './mentionHandles';

/** Exactly the inserters this needs, so the helper can be pinned without
 *  an editor. `PromptBodyEditorHandle` satisfies it structurally. */
export interface MentionInserters {
  insertImage: (image: PromptImageRef, opts?: { consumeMention?: boolean }) => void;
  insertAsset: (asset: MentionedAsset, opts?: { consumeMention?: boolean }) => void;
  /** Plain text at the caret WITHOUT consuming a pending `@query` — the
   *  registered wrapper passes `{ consumeMention: false }` (the editor's
   *  default would eat back to the last `@` within 80 characters; see the
   *  header of this file). The Prompts page's Insert positive relies on
   *  this. */
  insertText: (text: string) => void;
}

/**
 * A drop has NO pending `@query`, so the editor must not delete anything.
 *
 * The default consumes back to the last literal `@` within 80 characters of
 * the caret — right for the `@` picker, and destructive here: a body ending
 * `contact me at foo@bar.com` lost `@bar.com`, and with a second literal `@`
 * in that window the next insert took the previous chip with it.
 */
const AT_CARET = { consumeMention: false } as const;

/**
 * Why one item produced no chip.
 *
 * The resolver's three reasons plus one this path alone can hit: the target
 * card's body editor is not mounted. The registry hands back a handle keyed by
 * node id, and the wrappers `PromptNodeView` registers read their ref at CALL
 * time — so a card culled off-viewport between the aim and the commit answers
 * with a live-looking handle whose inserts throw.
 */
export type MentionFailure = AddReferenceFailure | 'editor_gone' | 'insert_failed';

export interface MentionLibraryResult {
  /** Chips actually written into the document. An asset contributes one; an
   *  image item contributes one per durable ref it resolved to. */
  mentioned: number;
  /** Typed, never silent — one entry per item that produced no chip. */
  failed: Array<{ item: LibraryItem; reason: MentionFailure }>;
}

/**
 * An insert that THREW wrote nothing, so it must not be counted as a chip.
 *
 * The inserters are host callbacks — `PromptNodeView` registers wrappers that
 * throw when the body editor is not mounted, precisely so this path reports a
 * refusal rather than a phantom success. Letting the throw escape would reject
 * the whole run and lose the items that DID land; catching it per item is what
 * makes "two inserted, one failed" representable.
 *
 * Returns `null` when the chip landed, else the typed reason. Only the
 * registry's own `EditorGoneError` is `editor_gone`; any other throw is an
 * inserter defect and is reported as `insert_failed` so the two cannot be
 * confused in a log or a toast.
 */
function tryInsert(run: () => void, label: string): MentionFailure | null {
  try {
    run();
    return null;
  } catch (err) {
    console.error(`[mentionLibraryItems] ${label} threw:`, err);
    return err instanceof EditorGoneError ? 'editor_gone' : 'insert_failed';
  }
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
      const failure = tryInsert(
        () =>
          handle.insertAsset({
            asset_id: item.id,
            name: item.title,
            // The library row's `kind` IS `asset_type` (`assetToLibraryItem`).
            asset_type: item.kind as AssetType,
            cover_file_id: cover,
            // ABSENT means NOT ASKED — the strip renders that differently from
            // an empty list, so a failed fetch must not become `[]` here.
            ...(refIds ? { ref_resource_ids: refIds } : {}),
          }, AT_CARET),
        'insertAsset',
      );
      if (failure === null) out.mentioned += 1;
      else out.failed.push({ item, reason: failure });
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
    // One refusal is enough to disqualify the ITEM: an asset-backed upload can
    // resolve to several refs, and reporting the same item once per failed ref
    // would inflate the count the caller says out loud.
    let refused: MentionFailure | null = null;
    for (const ref of refs) {
      const failure = tryInsert(
        () => handle.insertImage({ url: ref.url, alias: item.title, kind: ref.kind }, AT_CARET),
        'insertImage',
      );
      if (failure === null) out.mentioned += 1;
      else refused = refused ?? failure;
    }
    if (refused !== null) out.failed.push({ item, reason: refused });
  }
  return out;
}
