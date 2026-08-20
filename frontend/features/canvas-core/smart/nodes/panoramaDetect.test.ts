// IC panorama auto-detect (smart-canvas.js 10729): filenames/prompts with
// 360/全景/panorama/equirect/vr, or a ~2:1 equirect aspect, offer the
// 360° viewer.
import { describe, expect, it } from 'vitest';

import { isLikelyPanorama } from './panoramaDetect';

describe('isLikelyPanorama', () => {
  it('matches keyword names regardless of aspect', () => {
    expect(isLikelyPanorama('city_360.png')).toBe(true);
    expect(isLikelyPanorama('街景全景图.png')).toBe(true);
    expect(isLikelyPanorama('shot_panorama.jpg')).toBe(true);
    expect(isLikelyPanorama('equirect-render.png')).toBe(true);
  });

  it('matches ~2:1 equirect dimensions', () => {
    expect(isLikelyPanorama('img.png', 4096, 2048)).toBe(true);
    expect(isLikelyPanorama('img.png', 2000, 1050)).toBe(true);
  });

  it('rejects ordinary names and aspects', () => {
    expect(isLikelyPanorama('portrait.png', 1024, 1536)).toBe(false);
    expect(isLikelyPanorama('landscape.png', 1920, 1080)).toBe(false);
  });
});
