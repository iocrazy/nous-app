/**
 * Pure helper: render a human-readable expiry badge for a temp resource.
 *
 *   ttlDays=null      → "never expires"
 *   remaining > 1d    → "expires in N days"
 *   0 < remaining ≤1d → "expires today"
 *   remaining ≤ 0     → "expired"
 *   bad createdAt     → "" (callers should treat as "unknown")
 */
export function ttlBadgeText(createdAt: string, ttlDays: number | null): string {
  if (ttlDays === null) return 'never expires';
  const created = new Date(createdAt).getTime();
  if (Number.isNaN(created)) return '';
  const expiresAt = created + ttlDays * 24 * 60 * 60 * 1000;
  const remainingMs = expiresAt - Date.now();
  if (remainingMs <= 0) return 'expired';
  const remainingDays = Math.ceil(remainingMs / (24 * 60 * 60 * 1000));
  if (remainingDays === 1) return 'expires today';
  return `expires in ${remainingDays} days`;
}
