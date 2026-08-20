// features/canvas-core/smart/nodes/panoramaDetect.ts
//
// IC panorama auto-detect (smart-canvas.js:10729): a 360° viewer is offered
// when the name smells panoramic OR the image is ~2:1 (equirectangular).

const KEYWORDS = /360|全景|panorama|equirect|vr/i;

export function isLikelyPanorama(
  name: string,
  width?: number,
  height?: number,
): boolean {
  if (KEYWORDS.test(name)) return true;
  if (width && height && height > 0) {
    const ratio = width / height;
    return ratio >= 1.9 && ratio <= 2.1;
  }
  return false;
}
