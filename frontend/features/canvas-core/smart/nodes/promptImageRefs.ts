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
  splitMentionSegments,
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

/** A `@alias` occurrence only counts as a whole token when the character after
 *  it is not a word character. Aliases are minted as `Image N`, so without this
 *  `@Image 1` would happily match the first eight characters of `@Image 10`
 *  and repoint the reference at a different picture. */
const WORD_CHAR = /\w/;

/**
 * Build a prompt document from the PERSISTED form: the plain-text body plus the
 * separately-stored chip lists. The inverse of `docToPromptText`.
 *
 * ─ Why the chips have to be SPLICED, not appended ───────────────────────────
 *
 * `image_refs` records no position — `docToPromptText` flattened each chip to
 * the literal `@alias` and the text is the only place that offset survives. So
 * a chip is restored by consuming the token that stands for it, in `image_refs`
 * order, each chip taking the first occurrence no earlier chip has claimed.
 * Appending instead leaves the literal `@Image 1` mid-sentence AND drops a
 * duplicate chip after the last word: reopening a canvas visibly rewrote the
 * user's prompt (the bug this function exists to prevent).
 *
 * A chip whose token is nowhere in the body is still appended. It is a real
 * reference image, and silently dropping it would remove a picture from the
 * run — the failure mode `collectImageRefs` guards against at the other end.
 *
 * Blocks are recovered by splitting on '\n', which is what `docToPromptText`
 * joins them with; seeding one paragraph would lose every paragraph break.
 *
 * An asset token whose asset is not in `assets` stays LITERAL TEXT rather than
 * becoming a nameless chip. That keeps the round trip lossless — the token is
 * still in the projected text, so nothing is destroyed by a name table that
 * happened to arrive late — and it matches what the run does with the same
 * token (`renderMentionText` drops what it cannot name).
 */
export function seedPromptDoc(
  value: string,
  chips: readonly PromptImageRef[],
  assets: Map<string, MentionedAsset>,
): JSONContent {
  // Segments carrying their offset in `value`, so a claim made in one segment
  // can be compared against a claim made in another.
  let at = 0;
  const spans = splitMentionSegments(value).map((seg) => {
    const start = at;
    at += seg.kind === 'text' ? seg.text.length : assetMentionToken(seg.assetId).length;
    return { seg, start, end: at };
  });

  // Which `@alias` occurrence each chip takes. Searched only inside TEXT runs:
  // an asset token is opaque storage and must never be carved up by an alias
  // that happens to appear inside it.
  const claims: { start: number; end: number; chip: PromptImageRef }[] = [];
  const trailing: PromptImageRef[] = [];
  for (const chip of chips) {
    // An empty alias projects to a bare '@', which would match any mention the
    // user typed. Such a chip is appended rather than guessed at.
    if (!chip.alias) {
      trailing.push(chip);
      continue;
    }
    const token = `@${chip.alias}`;
    let placed = false;
    for (const span of spans) {
      if (span.seg.kind !== 'text') continue;
      const text = span.seg.text;
      for (let i = text.indexOf(token); i >= 0; i = text.indexOf(token, i + 1)) {
        const start = span.start + i;
        const end = start + token.length;
        const after = value[end];
        if (after !== undefined && WORD_CHAR.test(after)) continue;
        if (claims.some((c) => start < c.end && c.start < end)) continue;
        claims.push({ start, end, chip });
        placed = true;
        break;
      }
      if (placed) break;
    }
    if (!placed) trailing.push(chip);
  }
  claims.sort((a, b) => a.start - b.start);

  const blocks: JSONContent[][] = [[]];
  const pushNode = (node: JSONContent) => blocks[blocks.length - 1].push(node);
  /** Text may span a paragraph break; each '\n' opens a new block. */
  const pushText = (text: string) => {
    const lines = text.split('\n');
    for (const [i, line] of lines.entries()) {
      if (i > 0) blocks.push([]);
      if (line) pushNode({ type: 'text', text: line });
    }
  };

  let claimIdx = 0;
  for (const span of spans) {
    if (span.seg.kind === 'asset') {
      const known = assets.get(span.seg.assetId);
      if (known) pushNode({ type: PROMPT_ASSET_REF, attrs: { ...known } });
      else pushText(assetMentionToken(span.seg.assetId));
      continue;
    }
    let cursor = span.start;
    while (claimIdx < claims.length && claims[claimIdx].start < span.end) {
      const claim = claims[claimIdx];
      pushText(value.slice(cursor, claim.start));
      pushNode({ type: PROMPT_IMAGE_REF, attrs: { ...claim.chip } });
      cursor = claim.end;
      claimIdx += 1;
    }
    pushText(value.slice(cursor, span.end));
  }
  for (const chip of trailing) pushNode({ type: PROMPT_IMAGE_REF, attrs: { ...chip } });

  return {
    type: 'doc',
    content: blocks.map((content) => ({ type: 'paragraph', content })),
  };
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
    const refIds = attrs.ref_resource_ids;
    out.push({
      asset_id: id,
      name: attrs.name ?? '',
      asset_type: (attrs.asset_type ?? 'prop') as MentionedAsset['asset_type'],
      cover_file_id: attrs.cover_file_id ?? null,
      // OMITTED, not `[]`, when unknown — see `MentionedAsset`. An empty array
      // is a real answer that the strip is entitled to trust.
      ...(Array.isArray(refIds) ? { ref_resource_ids: refIds.map(String) } : {}),
    });
  });
  return out;
}
