// features/canvas-core/smart/loadPromptAsset.test.ts
// Phase 2 Task 3: pure builder that turns an AssetPromptPicker pick into a
// prompt-node patch + a new media node + the connection wiring them.

import { describe, expect, it } from 'vitest';

import { buildPromptAssetLoad } from './loadPromptAsset';
import { getResourceCoverUrl, type PromptAsset } from '../../../services/resourceService';

const ASSET: PromptAsset = {
  id: 'res-1',
  filename: 'hero.png',
  gen_prompt: 'a cinematic hero shot',
  gen_prompt_zh: '电影感英雄镜头',
  gen_prompt_negative: 'lowres, blurry',
  gen_prompt_negative_zh: '低分辨率，模糊',
  updated_at: '2026-07-26T00:00:00Z',
};

const PROMPT_NODE_ID = 'p1';
const PROMPT_NODE_POSITION = { x: 400, y: 200 };

describe('buildPromptAssetLoad', () => {
  it('builds patch from the chosen lang side, falling back to the other side', () => {
    // lang 'zh' with only an English prompt on the asset → falls back to gen_prompt.
    const enOnly: PromptAsset = { ...ASSET, gen_prompt_zh: null, gen_prompt_negative_zh: null };
    const fallback = buildPromptAssetLoad({
      asset: enOnly,
      lang: 'zh',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
    });
    expect(fallback.promptPatch.body).toBe(enOnly.gen_prompt);
    expect(fallback.promptPatch.negative_body).toBe(enOnly.gen_prompt_negative);

    // lang 'zh' with a zh prompt present → takes the zh side, not the fallback.
    const withZh = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'zh',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
    });
    expect(withZh.promptPatch.body).toBe(ASSET.gen_prompt_zh);
    expect(withZh.promptPatch.negative_body).toBe(ASSET.gen_prompt_negative_zh);

    // lang 'en' takes the en side.
    const withEn = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
    });
    expect(withEn.promptPatch.body).toBe(ASSET.gen_prompt);
    expect(withEn.promptPatch.negative_body).toBe(ASSET.gen_prompt_negative);
  });

  it('omits negative_body when the asset has no negative on either side', () => {
    const noNegative: PromptAsset = {
      ...ASSET,
      gen_prompt_negative: null,
      gen_prompt_negative_zh: null,
    };
    const en = buildPromptAssetLoad({
      asset: noNegative,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
    });
    expect('negative_body' in en.promptPatch).toBe(false);

    const zh = buildPromptAssetLoad({
      asset: noNegative,
      lang: 'zh',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
    });
    expect('negative_body' in zh.promptPatch).toBe(false);
  });

  it('media node carries cover url + filename and sits left of the prompt node', () => {
    const { mediaNode } = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
    });
    expect(mediaNode.type).toBe('media');
    expect(mediaNode.data.items).toHaveLength(1);
    expect(mediaNode.data.items[0]).toMatchObject({
      url: getResourceCoverUrl(ASSET.id),
      kind: 'image',
      name: ASSET.filename,
    });
    expect(mediaNode.position).toEqual({
      x: PROMPT_NODE_POSITION.x - 320,
      y: PROMPT_NODE_POSITION.y - 40,
    });
  });

  it('connection wires media → prompt with conn-<src>-<tgt> id', () => {
    const { mediaNode, connection } = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
    });
    expect(connection).toEqual({
      id: `conn-${mediaNode.id}-${PROMPT_NODE_ID}`,
      source: mediaNode.id,
      target: PROMPT_NODE_ID,
    });
  });
});
