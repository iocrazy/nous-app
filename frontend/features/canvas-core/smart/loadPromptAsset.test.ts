// features/canvas-core/smart/loadPromptAsset.test.ts
// Phase 2 Task 3: pure builder that turns an AssetPromptPicker pick into a
// prompt-node patch + a new media node + the connection wiring them.

import { describe, expect, it } from 'vitest';

import { buildPromptAssetLoad } from './loadPromptAsset';
import type { PromptAsset } from '../../../services/resourceService';

const MEDIA_URL = '/api/v1/generated-media/gm-1';

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
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect(fallback.promptPatch.body).toBe(enOnly.gen_prompt);
    expect(fallback.promptPatch.negative_body).toBe(enOnly.gen_prompt_negative);

    // lang 'zh' with a zh prompt present → takes the zh side, not the fallback.
    const withZh = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'zh',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect(withZh.promptPatch.body).toBe(ASSET.gen_prompt_zh);
    expect(withZh.promptPatch.negative_body).toBe(ASSET.gen_prompt_negative_zh);

    // lang 'en' takes the en side.
    const withEn = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
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
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect('negative_body' in en.promptPatch).toBe(false);

    const zh = buildPromptAssetLoad({
      asset: noNegative,
      lang: 'zh',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect('negative_body' in zh.promptPatch).toBe(false);
  });

  it('media node carries the caller-supplied mediaUrl + filename and sits left of the prompt node', () => {
    const { mediaNode } = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect(mediaNode.type).toBe('media');
    expect(mediaNode.data.items).toHaveLength(1);
    expect(mediaNode.data.items[0]).toMatchObject({
      url: MEDIA_URL,
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
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect(connection).toEqual({
      id: `conn-${mediaNode.id}-${PROMPT_NODE_ID}`,
      source: mediaNode.id,
      target: PROMPT_NODE_ID,
    });
  });

  // I1 (P3 final review): a video asset must produce a media node item
  // with kind 'video', not the old hardcoded 'image' — otherwise it
  // renders as a broken <img> and gets ignored by i2i source resolution.
  it('media node item kind follows the caller-supplied mediaKind', () => {
    const { mediaNode } = buildPromptAssetLoad({
      asset: ASSET,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'video',
    });
    expect(mediaNode.data.items[0]).toMatchObject({ kind: 'video' });
  });

  // I2 (P3 final review): negative-only assets (now reachable via the
  // four-column picker filter) must NOT wipe the prompt node's existing
  // body — promptPatch has to omit the `body` key entirely so the spread
  // at the call site is a no-op for it.
  it('omits body when the asset has no positive prompt on either side (negative-only asset)', () => {
    const negativeOnly: PromptAsset = {
      ...ASSET,
      gen_prompt: null,
      gen_prompt_zh: null,
    };
    const en = buildPromptAssetLoad({
      asset: negativeOnly,
      lang: 'en',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect('body' in en.promptPatch).toBe(false);
    expect(en.promptPatch.negative_body).toBe(negativeOnly.gen_prompt_negative);

    const zh = buildPromptAssetLoad({
      asset: negativeOnly,
      lang: 'zh',
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
    });
    expect('body' in zh.promptPatch).toBe(false);
    expect(zh.promptPatch.negative_body).toBe(negativeOnly.gen_prompt_negative_zh);
  });
});
