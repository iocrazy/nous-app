// "Follow the source image" as a real ratio value.
//
// Prompts used to be seeded with a hard-coded `ratio: '1:1'`, so generating
// from a 16:9 input produced a square unless the user noticed and changed it.
// `'auto'` says "match whatever is feeding this prompt", and is resolved to a
// concrete ratio at dispatch — the video side already ships the same idea as
// IC's 自适应.
//
// Resolution happens at dispatch rather than when the node is created: the
// wired input can change after the fact, and a value frozen at creation time
// would quietly stop matching.

import { RATIO_LABELS } from './nodes/GenFooterControls';

/** The sentinel meaning "follow the source image". */
export const AUTO_RATIO = 'auto';

/** Is this prompt following its source rather than a ratio the user picked?
 *  An unset value counts as auto — a prompt nobody has configured should
 *  match its input, not default to square. */
export function isAutoRatio(ratio: string | null | undefined): boolean {
  return !ratio || ratio === AUTO_RATIO;
}

const asNumber = (ratio: string): number => {
  const [w, h] = ratio.split(':').map(Number);
  return h > 0 ? w / h : NaN;
};

/** The offered ratio closest to a source image's shape, or null when the size
 *  is unusable. Only ever returns something the picker actually lists — a
 *  computed "1.63:1" would be unselectable and unexplainable in the UI. */
export function nearestRatio(width: number, height: number): string | null {
  if (!(width > 0) || !(height > 0)) return null;
  const target = width / height;
  let best: string | null = null;
  let bestDelta = Number.POSITIVE_INFINITY;
  for (const candidate of Object.keys(RATIO_LABELS)) {
    const value = asNumber(candidate);
    if (!Number.isFinite(value)) continue;
    const delta = Math.abs(value - target);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = candidate;
    }
  }
  return best;
}

/** How long to wait for the source image before giving up on matching it. */
export const MEASURE_TIMEOUT_MS = 3000;

/** Measure an image and map it onto an offered ratio.
 *  Resolves to null if the image cannot be loaded — the caller then leaves the
 *  ratio out entirely rather than sending a guess.
 *
 *  ⚠️ The timeout is not decoration. A URL that neither loads nor errors
 *  leaves `onload`/`onerror` unfired forever, and since the dispatch awaits
 *  this, the generation request would never be sent — the run would silently
 *  never start. "The awaited transition may never happen" needs an explicit
 *  branch, not an implicit hope. */
export function measureRatio(url: string): Promise<string | null> {
  return new Promise((resolve) => {
    if (typeof Image === 'undefined') {
      resolve(null);
      return;
    }
    let settled = false;
    const finish = (value: string | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), MEASURE_TIMEOUT_MS);
    const img = new Image();
    // Measuring needs no pixel access, so no CORS handshake is required here.
    img.onload = () => finish(nearestRatio(img.naturalWidth, img.naturalHeight));
    img.onerror = () => finish(null);
    img.src = url;
  });
}
