import { describe, expect, it } from 'vitest';

import { swapEditedImage } from './swapEditedImage';

const TO = { url: '/api/v1/generated-media/901/cover', id: '901' };

describe('swapEditedImage', () => {
  it('replaces the primary preview and drops what described the old picture', () => {
    expect(
      swapEditedImage(
        {
          preview_url: '/api/v1/generated-media/5/cover',
          images: [{ url: '/api/v1/generated-media/5/cover', kind: 'image', name: 'a.png' }],
        },
        '/api/v1/generated-media/5/cover',
        TO,
      ),
    ).toEqual({
      preview_url: TO.url,
      resource_id: null,
      crop_region: null,
      images: [{ url: TO.url, kind: 'image', name: 'a.png', id: '901' }],
    });
  });

  it('replaces only the grid image that was edited', () => {
    const images = [
      { url: '/api/v1/generated-media/5/cover', kind: 'image' as const },
      { url: '/api/v1/generated-media/6/cover', kind: 'image' as const, name: 'mask.png' },
    ];
    expect(
      swapEditedImage(
        { preview_url: '/api/v1/generated-media/5/cover', images },
        '/api/v1/generated-media/6/cover',
        TO,
      ),
    ).toEqual({
      images: [images[0], { url: TO.url, kind: 'image', name: 'mask.png', id: '901' }],
    });
    expect(images[1].url).toBe('/api/v1/generated-media/6/cover');
  });

  it('a history node with no preview_url swaps inside images', () => {
    expect(
      swapEditedImage(
        { preview_url: null, images: [{ url: '/x/5', kind: 'image' }] },
        '/x/5',
        TO,
      ),
    ).toEqual({ images: [{ url: TO.url, kind: 'image', id: '901' }] });
  });

  it('never returns a no-op: an unmatched source appends the product', () => {
    expect(swapEditedImage({ preview_url: '/x/1', images: null }, '/x/2', TO)).toEqual({
      images: [{ url: TO.url, kind: 'image', id: '901' }],
    });
  });
});
