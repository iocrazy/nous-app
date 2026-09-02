/**
 * Pure builder for "load a Library asset into the canvas" (Phase 2 Task 3
 * of spec 2026-07-26-asset-prompt-management).
 *
 * The Library picker (AssetPromptPicker) hands back a PromptAsset + the
 * lang side the user was previewing. This turns that pick into the three
 * pieces PromptNodeView needs to apply: a patch for the prompt node itself,
 * a new media node carrying the asset's cover as a visual reference/
 * thumbnail, and the connection wiring that media node into the prompt.
 *
 * The media node's `items[0].url` is caller-supplied (`mediaUrl`) rather
 * than derived here: when the caller has minted a durable
 * /api/v1/generated-media/ URL (via importResourceAsCanvasMedia) that URL
 * makes the node a real i2i/i2v source (promptInputs.ts's DURABLE_PREFIXES
 * accepts it). When minting fails, the caller falls back to the resource
 * cover URL instead — that fallback is visual-only (not a matching prefix,
 * so it's inert as a run input), but keeps the picked asset visible on
 * the canvas.
 *
 * No store access here — PromptNodeView owns applying the result
 * (setNodes/setConnections/patch), which keeps this function trivial to
 * unit test.
 */

import { createMediaNode } from './factories';
import type { MediaNode } from './types';
import type { PromptAsset } from '../../../services/resourceService';

export function buildPromptAssetLoad(args: {
  asset: PromptAsset;
  lang: 'en' | 'zh';
  promptNodeId: string;
  promptNodePosition: { x: number; y: number };
  mediaUrl: string;
  /** Kind of the media the picker resolved (importResourceAsCanvasMedia's
   *  result, or 'image' for the cover-fallback path — the cover endpoint
   *  only ever serves images). Drives the new media node's item kind so
   *  video assets don't render as a broken <img> and don't get treated as
   *  an image i2i source. */
  mediaKind: 'image' | 'video';
}): {
  promptPatch: { body?: string; negative_body?: string };
  mediaNode: MediaNode;
  connection: { id: string; source: string; target: string };
} {
  const { asset, lang, promptNodeId, promptNodePosition, mediaUrl, mediaKind } = args;

  // Side-picking: prefer the chosen lang, fall back to the other side when
  // that side is empty (e.g. only an English prompt was ever written).
  const body =
    (lang === 'zh' ? asset.gen_prompt_zh ?? asset.gen_prompt : asset.gen_prompt ?? asset.gen_prompt_zh) ?? '';
  const negative =
    lang === 'zh'
      ? asset.gen_prompt_negative_zh ?? asset.gen_prompt_negative
      : asset.gen_prompt_negative ?? asset.gen_prompt_negative_zh;

  const promptPatch: { body?: string; negative_body?: string } = {
    // Omit the key entirely when the asset has no positive prompt on either
    // side (negative-only assets, now reachable via the four-column picker
    // filter) — an empty string would blow away whatever the user already
    // typed into the node, since promptPatch is spread over the existing
    // body at the call site.
    ...(body.trim() ? { body } : {}),
    // Omit the key entirely when neither side has a negative prompt — an
    // empty string would render the (empty) negative textarea for no reason.
    ...(negative ? { negative_body: negative } : {}),
  };

  const mediaNode = createMediaNode(
    {
      items: [{ url: mediaUrl, kind: mediaKind, name: asset.filename }],
    },
    {
      // Sits to the left of (and slightly above) the prompt node so the
      // auto-wired connection reads left-to-right without overlapping it.
      position: { x: promptNodePosition.x - 320, y: promptNodePosition.y - 40 },
    },
  );

  const connection = {
    id: `conn-${mediaNode.id}-${promptNodeId}`,
    source: mediaNode.id,
    target: promptNodeId,
  };

  return { promptPatch, mediaNode, connection };
}
