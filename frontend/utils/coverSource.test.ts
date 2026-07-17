import { describe, it, expect } from 'vitest';
import { pickCoverFrame } from './coverSource';

// Remote CDN slide URLs (douyin signed URLs) expire; the local downloaded
// cover never does. The default frame must prefer the local cover — cards
// were rendering "Image unavailable" for albums whose local cover existed
// because they hot-linked the expired remote slide instead.
describe('pickCoverFrame', () => {
  const images = ['https://cdn/slide0.jpg', 'https://cdn/slide1.jpg'];

  it('prefers the local cover for the default frame', () => {
    expect(pickCoverFrame('/api/cover.jpg', images, 0)).toBe('/api/cover.jpg');
  });

  it('falls back to the first remote slide when no local cover', () => {
    expect(pickCoverFrame(undefined, images, 0)).toBe('https://cdn/slide0.jpg');
  });

  it('uses the remote slide for flipped frames (only the cover is local)', () => {
    expect(pickCoverFrame('/api/cover.jpg', images, 1)).toBe('https://cdn/slide1.jpg');
  });

  it('returns undefined when nothing is available', () => {
    expect(pickCoverFrame(undefined, [], 0)).toBeUndefined();
  });

  it('defaults to frame 0 when index omitted', () => {
    expect(pickCoverFrame('/api/cover.jpg', images)).toBe('/api/cover.jpg');
  });
});
