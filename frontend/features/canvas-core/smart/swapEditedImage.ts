// features/canvas-core/smart/swapEditedImage.ts
//
// An in-place edit (crop) replaces THE image that was edited — the primary
// preview, one grid item, or an item of a history node that has no preview —
// with its derived copy. Pure: returns the node-data patch.

import type { GeneratedImageRef } from './types';

export interface EditedImagePatch {
  preview_url?: string;
  /** Cleared with the primary: all three only ever described the old picture.
   *  `gen_ratio` is the aspect the RUN asked for — keep it and the card boxes
   *  the cropped image at the uncropped ratio, letterboxing it. */
  resource_id?: null;
  crop_region?: null;
  gen_ratio?: null;
  images?: GeneratedImageRef[];
}

export function swapEditedImage(
  slots: { preview_url?: string | null; images?: GeneratedImageRef[] | null },
  from: string,
  to: { url: string; id: string },
): EditedImagePatch {
  const patch: EditedImagePatch = {};
  if (slots.preview_url === from) {
    patch.preview_url = to.url;
    patch.resource_id = null;
    patch.crop_region = null;
    patch.gen_ratio = null;
  }
  const images = Array.isArray(slots.images) ? slots.images : [];
  if (images.some((img) => img?.url === from)) {
    patch.images = images.map((img) =>
      img?.url === from ? { ...img, url: to.url, id: to.id } : img,
    );
  } else if (patch.preview_url === undefined) {
    // Nothing matched: keep the product rather than let the edit vanish.
    patch.images = [...images, { url: to.url, kind: 'image', id: to.id }];
  }
  return patch;
}
