/**
 * Per-port-type ink accent tokens for ClassicMode handles (Phase 5a B4).
 *
 * Each typed port renders a small handle dot tinted by its type so the
 * graph reads at a glance. Distinct steps on the ink scale keep us inside
 * the theme system (CI guard: ink tokens only, no legacy grey scale). The `!` prefix
 * wins over React Flow's default handle background.
 */

import type { ClassicPortType } from '../registry';

export const CLASSIC_PORT_TONE: Record<ClassicPortType, string> = {
  image: '!bg-ink-200',
  text: '!bg-ink-500',
  prompt: '!bg-ink-700',
  // `video` is the brightest/most-saturated accent so an animated artifact
  // reads as the "richest" wire at a glance — distinct from the other three.
  video: '!bg-ink-50',
};

/** Vertical layout constants shared by the handle dots + their labels. */
export const PORT_HEADER_OFFSET = 34;
export const PORT_ROW_GAP = 20;
