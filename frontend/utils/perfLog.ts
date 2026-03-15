/**
 * Simple performance logger that stores timing data visible on mobile.
 * Logs are accessible via window.__perf and displayed in a floating overlay.
 * Remove this file once debugging is complete.
 */

interface PerfEntry {
  label: string;
  time: number;   // ms since page load
  delta?: number;  // ms since previous entry
}

const entries: PerfEntry[] = [];
const startTime = performance.now();

export function perfMark(label: string): void {
  const now = performance.now();
  const elapsed = Math.round(now - startTime);
  const prev = entries[entries.length - 1];
  const delta = prev ? elapsed - prev.time : 0;
  entries.push({ label, time: elapsed, delta });
  console.log(`[perf] ${label}: ${elapsed}ms (+${delta}ms)`);
}

export function getPerfEntries(): PerfEntry[] {
  return [...entries];
}

// Make accessible from console
if (typeof window !== 'undefined') {
  (window as any).__perf = { entries, mark: perfMark, show: getPerfEntries };
}
