// frontend/components/TopicInspiration/hotspotRanking.ts
import type { Hotspot } from '../../services/topicService';

// "What's actually hot" blends the LLM's relevance score with the objective
// heat signal (board rank + persistence). Both are 0..1. Using both prevents a
// single LLM call's opinion from floating a low-traffic item to the top, and
// surfaces genuinely viral items even if the LLM under-scored them.
const SCORE_WEIGHT = 0.5;
const HEAT_WEIGHT = 0.5;

export function blendedScore(h: Pick<Hotspot, 'score' | 'heat'>): number {
  const score = typeof h.score === 'number' ? h.score : null;
  const heat = typeof h.heat === 'number' ? h.heat : null;
  if (score === null && heat === null) return 0;
  if (score === null) return heat as number;
  if (heat === null) return score;
  return SCORE_WEIGHT * score + HEAT_WEIGHT * heat;
}

// Below this blended score a SCORED item is treated as low-signal noise.
export const LOW_SIGNAL_THRESHOLD = 0.3;

// Split the feed into the main list and folded low-signal noise. An item is
// noise ONLY if it has actually been scored (score is a number) and its blended
// score is below the threshold — unscored items (pending the scorer, or no heat
// yet) are NOT hidden, so we never bury fresh items we just haven't judged.
export function partitionBySignal(
  hotspots: Hotspot[],
  threshold: number = LOW_SIGNAL_THRESHOLD,
): { main: Hotspot[]; low: Hotspot[] } {
  const main: Hotspot[] = [];
  const low: Hotspot[] = [];
  for (const h of hotspots) {
    const scored = typeof h.score === 'number';
    if (scored && blendedScore(h) < threshold) low.push(h);
    else main.push(h);
  }
  return { main, low };
}

// Highest blended-rank hotspots first; ties broken by raw heat then score.
export function topHotspots(hotspots: Hotspot[], n: number): Hotspot[] {
  return hotspots
    .filter((h) => typeof h.score === 'number' || typeof h.heat === 'number')
    .slice()
    .sort((a, b) => {
      const d = blendedScore(b) - blendedScore(a);
      if (d !== 0) return d;
      return (b.heat ?? 0) - (a.heat ?? 0);
    })
    .slice(0, n);
}
