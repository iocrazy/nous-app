// features/canvas-core/smart/promptReferenceMedia.test.ts
// Pure builder that turns a Send-to-Canvas payload's picture into a media
// node + the connection wiring it into the freshly created prompt node.
// Ported from the picker-era builder's tests, retired in P3 (spec
// 2026-09-05 §3.7): the lang-fallback cases died with the picker that fed
// them; the node and connection shape lives on here.

import { describe, expect, it } from 'vitest';

import { buildPromptReferenceMedia } from './promptReferenceMedia';

const MEDIA_URL = '/api/v1/generated-media/gm-1';
const NAME = 'hero.png';

const PROMPT_NODE_ID = 'p1';
const PROMPT_NODE_POSITION = { x: 400, y: 200 };

describe('buildPromptReferenceMedia', () => {
  it('media node carries the caller-supplied mediaUrl + name and sits left of the prompt node', () => {
    const { mediaNode } = buildPromptReferenceMedia({
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
      name: NAME,
    });
    expect(mediaNode.type).toBe('media');
    expect(mediaNode.data.items).toHaveLength(1);
    expect(mediaNode.data.items[0]).toMatchObject({
      url: MEDIA_URL,
      kind: 'image',
      name: NAME,
    });
    expect(mediaNode.position).toEqual({
      x: PROMPT_NODE_POSITION.x - 320,
      y: PROMPT_NODE_POSITION.y - 40,
    });
  });

  it('connection wires media → prompt with conn-<src>-<tgt> id', () => {
    const { mediaNode, connection } = buildPromptReferenceMedia({
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'image',
      name: NAME,
    });
    expect(connection).toEqual({
      id: `conn-${mediaNode.id}-${PROMPT_NODE_ID}`,
      source: mediaNode.id,
      target: PROMPT_NODE_ID,
    });
  });

  // I1 (P3 final review of the 2026-07-26 spec): a video must produce a
  // media node item with kind 'video', not a hardcoded 'image' — otherwise
  // it renders as a broken <img> and gets ignored by i2i source resolution.
  it('media node item kind follows the caller-supplied mediaKind', () => {
    const { mediaNode } = buildPromptReferenceMedia({
      promptNodeId: PROMPT_NODE_ID,
      promptNodePosition: PROMPT_NODE_POSITION,
      mediaUrl: MEDIA_URL,
      mediaKind: 'video',
      name: NAME,
    });
    expect(mediaNode.data.items[0]).toMatchObject({ kind: 'video' });
  });
});
