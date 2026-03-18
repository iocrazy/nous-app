// ─── Aspect Ratio Detection ──────────────────────────────────────────────────

const COMMON_RATIOS: Array<{ w: number; h: number; label: string }> = [
  { w: 16, h: 9, label: '16:9' },
  { w: 9, h: 16, label: '9:16' },
  { w: 4, h: 3, label: '4:3' },
  { w: 3, h: 4, label: '3:4' },
  { w: 1, h: 1, label: '1:1' },
  { w: 3, h: 2, label: '3:2' },
  { w: 2, h: 3, label: '2:3' },
  { w: 21, h: 9, label: '21:9' },
];

const TOLERANCE = 0.02;

/**
 * Detect the closest common aspect ratio for given dimensions.
 * Returns a string like "16:9" or the raw reduced ratio (e.g. "1920:1080" → "16:9").
 */
export function detectAspectRatio(width: number, height: number): string {
  if (width <= 0 || height <= 0) return '';

  const ratio = width / height;

  // Try matching a common ratio within tolerance
  for (const { w, h, label } of COMMON_RATIOS) {
    if (Math.abs(ratio - w / h) < TOLERANCE) {
      return label;
    }
  }

  // Fall back to GCD-reduced ratio
  const g = gcd(width, height);
  return `${width / g}:${height / g}`;
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

/**
 * Format image dimensions with aspect ratio for display.
 * Example: "1920 × 1080 · 16:9"
 */
export function formatDimensionsWithRatio(width: number, height: number): string {
  const ratio = detectAspectRatio(width, height);
  const dims = `${width} × ${height}`;
  return ratio ? `${dims} · ${ratio}` : dims;
}
