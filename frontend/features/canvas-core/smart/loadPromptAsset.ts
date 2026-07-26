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
 * That media node is NOT wired into the i2i/i2v pipeline: generation's
 * source resolution (promptInputs.ts's DURABLE_PREFIX, '/api/v1/generated-media/')
 * only accepts durable generated-media URLs, and the resource cover URL
 * this factory uses doesn't match that prefix, so it's inert as a run
 * input today — purely a canvas thumbnail. Wiring it up for real would
 * need importCanvasMedia (or equivalent) to mint a durable generated-media
 * URL for the asset's cover first; deferred.
 *
 * No store access here — PromptNodeView owns applying the result
 * (setNodes/setConnections/patch), which keeps this function trivial to
 * unit test.
 */

import { createMediaNode } from './factories';
import type { MediaNode } from './types';
import { getResourceCoverUrl, type PromptAsset } from '../../../services/resourceService';

export function buildPromptAssetLoad(args: {
  asset: PromptAsset;
  lang: 'en' | 'zh';
  promptNodeId: string;
  promptNodePosition: { x: number; y: number };
}): {
  promptPatch: { body: string; negative_body?: string };
  mediaNode: MediaNode;
  connection: { id: string; source: string; target: string };
} {
  const { asset, lang, promptNodeId, promptNodePosition } = args;

  // Side-picking: prefer the chosen lang, fall back to the other side when
  // that side is empty (e.g. only an English prompt was ever written).
  const body =
    (lang === 'zh' ? asset.gen_prompt_zh ?? asset.gen_prompt : asset.gen_prompt ?? asset.gen_prompt_zh) ?? '';
  const negative =
    lang === 'zh'
      ? asset.gen_prompt_negative_zh ?? asset.gen_prompt_negative
      : asset.gen_prompt_negative ?? asset.gen_prompt_negative_zh;

  const promptPatch: { body: string; negative_body?: string } = {
    body,
    // Omit the key entirely when neither side has a negative prompt — an
    // empty string would render the (empty) negative textarea for no reason.
    ...(negative ? { negative_body: negative } : {}),
  };

  const mediaNode = createMediaNode(
    {
      items: [{ url: getResourceCoverUrl(asset.id), kind: 'image', name: asset.filename }],
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
