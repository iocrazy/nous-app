// features/canvas-core/smart/refOrder.ts
//
// IC reorderManualInputRefs: drag a manual reference thumbnail onto another
// to reorder. Order is FUNCTIONAL, not cosmetic — it decides which image
// becomes image_1 / image_2… in the generation request.

export interface OrderableRef {
  url: string;
  kind?: string;
}

/** Move `fromUrl` before/after `toUrl`. Unknown urls → unchanged list. */
export function reorderRefs<T extends OrderableRef>(
  refs: T[],
  fromUrl: string,
  toUrl: string,
  before: boolean,
): T[] {
  const fromIdx = refs.findIndex((r) => r.url === fromUrl);
  const toIdx = refs.findIndex((r) => r.url === toUrl);
  if (fromIdx < 0 || toIdx < 0 || fromUrl === toUrl) return refs;
  const next = refs.slice();
  const [moved] = next.splice(fromIdx, 1);
  const anchor = next.findIndex((r) => r.url === toUrl);
  next.splice(before ? anchor : anchor + 1, 0, moved);
  return next;
}

/** IC SMART_REFERENCE_IMAGE_MAX. */
export const MAX_REFERENCE_IMAGES = 20;
