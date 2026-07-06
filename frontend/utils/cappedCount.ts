/**
 * Sidebar count formatting for the capped count RPC (mig 342): the server
 * counts at most 100_001 rows — beyond the cap the exact number stops being
 * useful and costs hundreds of ms per call at million-file scale, so the UI
 * shows "100,000+" instead.
 */

export const COUNT_CAP = 100_000;

export function formatCappedCount(count: number): string {
  if (count > COUNT_CAP) return `${COUNT_CAP.toLocaleString()}+`;
  return count.toLocaleString();
}
