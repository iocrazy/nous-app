// Reading image chips back out of a prompt document.
//
// IC parity (`collectMentionedImagesFromPrompt`): the prompt box owns the
// reference list. A chip sitting in the text IS a reference image — delete the
// chip and the image leaves the run. Keeping the list in the document (rather
// than in a side field the text merely annotates) is what makes that true
// without any extra bookkeeping.

import type { JSONContent } from '@tiptap/core';

import {
  assetMentionToken,
  type MentionedAsset,
} from '../mentionedAssets';

/** The tiptap node name for an inline image chip. */
export const PROMPT_IMAGE_REF = 'promptImageRef';

/** An image chip embedded in a prompt body. */
export interface PromptImageRef {
  url: string;
  alias: string;
  kind: string;
}

function walk(node: JSONContent, visit: (n: JSONContent) => void): void {
  visit(node);
  for (const child of node.content ?? []) walk(child, visit);
}

/** Every image chip in the document, in reading order, deduped by url.
 *  A chip with no url is skipped — a reference the generation link could not
 *  use is worse than no reference, because it looks like it worked. */
export function collectImageRefs(doc: JSONContent): PromptImageRef[] {
  const seen = new Set<string>();
  const out: PromptImageRef[] = [];
  walk(doc, (n) => {
    if (n.type !== PROMPT_IMAGE_REF) return;
    const attrs = (n.attrs ?? {}) as Partial<PromptImageRef>;
    const url = typeof attrs.url === 'string' ? attrs.url : '';
    if (!url || seen.has(url)) return;
    seen.add(url);
    out.push({ url, alias: attrs.alias ?? '', kind: attrs.kind ?? 'image' });
  });
  return out;
}

/** The prompt as it is PERSISTED: image chips render as `@alias`, asset chips
 *  as their `@[asset:id]` token, block-level nodes newline-separated.
 *
 *  ⚠️ Not what the model receives. The asset token is a storage form — it is
 *  swapped for the asset's name by `promptBodyForRun` at dispatch, which is
 *  the only reader allowed to build a request body. */
export function docToPromptText(doc: JSONContent): string {
  const blocks: string[] = [];
  for (const block of doc.content ?? []) {
    let line = '';
    walk(block, (n) => {
      if (n.type === PROMPT_IMAGE_REF) {
        const alias = ((n.attrs ?? {}) as Partial<PromptImageRef>).alias ?? '';
        line += `@${alias}`;
      } else if (n.type === PROMPT_ASSET_REF) {
        const id = ((n.attrs ?? {}) as Partial<MentionedAsset>).asset_id ?? '';
        // A chip with no id projects to nothing rather than to a malformed
        // token: `@[asset:]` would not parse back into a chip on reload and
        // would survive into the run text as literal noise.
        line += id ? assetMentionToken(id) : '';
      } else if (n.type === 'text') {
        line += n.text ?? '';
      }
    });
    blocks.push(line);
  }
  return blocks.join('\n');
}

/** Matches '@' plus the word characters after it, at the end of the string. */
const AT_BEFORE_CARET = /@(\w*)$/;

/** The active `@query` in the text immediately before the caret, or null when
 *  there is no live mention token there.
 *
 *  This is what makes the picker track typing: '@' opens it, each following
 *  keystroke narrows it, and deleting the '@' returns null — which is the
 *  signal to close. Kept as a pure function of the text so the rule is
 *  testable without driving a contenteditable. */
export function mentionQueryFromText(textBeforeCaret: string): string | null {
  const match = AT_BEFORE_CARET.exec(textBeforeCaret);
  return match ? match[1] : null;
}

// ─── Asset mentions (inline chips naming an asset-library entity) ───────────
//
// The SECOND chip family in a prompt body, and deliberately a separate node
// type rather than a flavour of the image chip. An image chip names a picture
// already available to this node; an asset chip names a LIBRARY ENTITY whose
// files are fetched at run time. They project into the text differently too —
// an image chip renders as its alias, an asset chip as a stable id token — so
// collapsing them would make the projection ambiguous.

/** The tiptap node name for an inline asset chip. */
export const PROMPT_ASSET_REF = 'promptAssetRef';

/** Every asset chip in the document, in reading order, deduped by asset id.
 *  A chip with no id is skipped: it could not be bundled, and a chip that
 *  looks like a reference while contributing nothing is the silent-drop
 *  failure the image-chip collector guards against for the same reason. */
export function collectAssetRefs(doc: JSONContent): MentionedAsset[] {
  const seen = new Set<string>();
  const out: MentionedAsset[] = [];
  walk(doc, (n) => {
    if (n.type !== PROMPT_ASSET_REF) return;
    const attrs = (n.attrs ?? {}) as Partial<MentionedAsset>;
    const id = typeof attrs.asset_id === 'string' ? attrs.asset_id : '';
    if (!id || seen.has(id)) return;
    seen.add(id);
    out.push({
      asset_id: id,
      name: attrs.name ?? '',
      asset_type: (attrs.asset_type ?? 'prop') as MentionedAsset['asset_type'],
      cover_file_id: attrs.cover_file_id ?? null,
    });
  });
  return out;
}
