// frontend/components/TopicInspiration/sourceHealthSummary.ts
import type { SourceHealth, SourceHealthStatus } from '../../services/topicService';

export interface HealthSummary {
  ok: number;
  degraded: number;
  dead: number;
  total: number;
  // Worst status among ENABLED sources — drives the badge color. Disabled
  // sources are excluded: an intentionally-off feed is not a fault.
  worst: SourceHealthStatus;
}

// Worst-first severity so the badge reflects the most urgent state.
const SEVERITY: Record<SourceHealthStatus, number> = { ok: 0, degraded: 1, dead: 2 };

export function summarizeSourceHealth(sources: SourceHealth[]): HealthSummary {
  const enabled = sources.filter((s) => s.enabled);
  let worst: SourceHealthStatus = 'ok';
  const counts = { ok: 0, degraded: 0, dead: 0 };
  for (const s of enabled) {
    const status = (['ok', 'degraded', 'dead'] as const).includes(s.health)
      ? s.health
      : 'ok';
    counts[status] += 1;
    if (SEVERITY[status] > SEVERITY[worst]) worst = status;
  }
  return { ...counts, total: enabled.length, worst };
}
