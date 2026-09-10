// features/canvas-core/smart/mediaEditBridge.test.ts
// B3: media items live in generated_media; genIdFromDurableUrl parses the id
// out of the durable url.

import { describe, expect, it } from 'vitest';

import { genIdFromDurableUrl } from './mediaEditBridge';

describe('genIdFromDurableUrl', () => {
  it.each([
    ['/api/v1/generated-media/123/file', '123'],
    ['/api/v1/generated-media/456/cover', '456'],
    ['/api/v1/generated-media/789/stream', '789'],
    ['https://api.x.com/api/v1/generated-media/42/file', '42'],
  ])('%s → %s', (url, id) => {
    expect(genIdFromDurableUrl(url)).toBe(id);
  });
  it('non-durable urls → null', () => {
    expect(genIdFromDurableUrl('https://cdn/x.png')).toBeNull();
    expect(genIdFromDurableUrl('/api/v1/resources/1/file')).toBeNull();
  });
});
