/**
 * Pure geometry helpers for the outpaint tool (Phase 3 Day 14).
 *
 * Padding is per-side, each a fraction of the source dimension on
 * that axis — mirroring backend `image_outpaint.py` so the editor
 * can't request an extension the derive endpoint would reject.
 *
 * All helpers are immutable.
 */

export interface OutpaintPadding {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export type OutpaintSide = keyof OutpaintPadding;

/** Mirrors backend MAX_PAD_PER_SIDE. */
export const MAX_PAD_PER_SIDE = 2.0;

export const ZERO_PADDING: OutpaintPadding = Object.freeze({
  left: 0,
  top: 0,
  right: 0,
  bottom: 0,
});

function clampPad(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(MAX_PAD_PER_SIDE, Math.max(0, value));
}

/** Return a copy with `side` set to the clamped `value`. */
export function setSide(
  padding: OutpaintPadding,
  side: OutpaintSide,
  value: number,
): OutpaintPadding {
  return { ...padding, [side]: clampPad(value) };
}

/** True when at least one side extends — the endpoint rejects
 *  zero-everywhere padding. */
export function hasExtension(padding: OutpaintPadding): boolean {
  return (
    padding.left > 0 ||
    padding.top > 0 ||
    padding.right > 0 ||
    padding.bottom > 0
  );
}

/** Output pixel size for a given source size — mirrors the backend's
 *  `int(round(...))` so the live readout matches the real result. */
export function targetResolution(
  padding: OutpaintPadding,
  natural: { width: number; height: number },
): { width: number; height: number } {
  return {
    width:
      natural.width +
      Math.round(padding.left * natural.width) +
      Math.round(padding.right * natural.width),
    height:
      natural.height +
      Math.round(padding.top * natural.height) +
      Math.round(padding.bottom * natural.height),
  };
}

export interface AspectPreset {
  label: string;
  /** width / height. */
  value: number;
}

export const ASPECT_PRESETS: ReadonlyArray<AspectPreset> = [
  { label: '16:9', value: 16 / 9 },
  { label: '9:16', value: 9 / 16 },
  { label: '1:1', value: 1 },
  { label: '4:3', value: 4 / 3 },
];

/**
 * Symmetric padding that extends the source to `aspect` (width /
 * height) by growing ONE axis — the wider axis stays untouched.
 * Returns ZERO_PADDING when the source already matches or the needed
 * padding exceeds MAX_PAD_PER_SIDE (the preset can't express it).
 */
export function paddingForAspect(
  aspect: number,
  natural: { width: number; height: number },
): OutpaintPadding {
  if (
    !Number.isFinite(aspect) ||
    aspect <= 0 ||
    natural.width <= 0 ||
    natural.height <= 0
  ) {
    return ZERO_PADDING;
  }
  const current = natural.width / natural.height;
  if (Math.abs(current - aspect) < 1e-6) return ZERO_PADDING;

  if (aspect > current) {
    // Need to grow width: total extra = aspect * h - w.
    const extra = (aspect * natural.height - natural.width) / natural.width;
    const perSide = extra / 2;
    if (perSide > MAX_PAD_PER_SIDE) return ZERO_PADDING;
    return { ...ZERO_PADDING, left: perSide, right: perSide };
  }
  // Need to grow height: total extra = w / aspect - h.
  const extra = (natural.width / aspect - natural.height) / natural.height;
  const perSide = extra / 2;
  if (perSide > MAX_PAD_PER_SIDE) return ZERO_PADDING;
  return { ...ZERO_PADDING, top: perSide, bottom: perSide };
}
