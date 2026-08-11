/**
 * Storyboard shot vocabularies — MIRRORED from the backend so the parameter-pill
 * cycling offers exactly the values the Auto Storyboard prompt constrains the
 * model to. Kept in sync BY HAND (do NOT import backend across the stack).
 *
 * Source of truth:
 *   backend/app/services/storyboard/script/script_ai_service.py
 *     SHOT_TYPES / CAMERA_ANGLES / CAMERA_MOVEMENTS
 * If those tuples change, update these arrays to match.
 *
 * Moved here from `editor/storyboard/vocab.ts` (Task 6, 2026-08-11) when the
 * editor's storyboard rail retired — `ShotNodeView.tsx` (the canvas shot
 * node's parameter pills) was and is the sole consumer, so this now lives
 * inside canvas-core with it instead of reaching into `editor/`.
 */

export const SHOT_TYPES = ['WIDE', 'MEDIUM', 'CLOSE', 'ECU', 'OTS', 'POV', 'INSERT'] as const;
export const CAMERA_ANGLES = ['EYE', 'LOW', 'HIGH', 'DUTCH', 'TOP'] as const;
export const CAMERA_MOVEMENTS = ['STATIC', 'PAN', 'TILT', 'DOLLY', 'TRACK', 'HANDHELD'] as const;

/** The shot fields that cycle through a fixed vocabulary on pill click. */
export const SHOT_PARAM_VOCAB = {
  shot_type: SHOT_TYPES,
  camera_angle: CAMERA_ANGLES,
  camera_movement: CAMERA_MOVEMENTS,
} as const;

export type ShotParamField = keyof typeof SHOT_PARAM_VOCAB;

/**
 * Next value in a vocabulary after `current` (wraps). An absent/unknown current
 * value starts the cycle at the first entry — so a fresh, tag-less shot's first
 * click lands a real value rather than a no-op.
 */
export function cycleVocab(vocab: readonly string[], current: string | null | undefined): string {
  if (!current) return vocab[0];
  const idx = vocab.indexOf(current);
  if (idx === -1) return vocab[0];
  return vocab[(idx + 1) % vocab.length];
}
