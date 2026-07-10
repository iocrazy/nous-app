// features/canvas-core/smart/loopVars.ts
//
// Loop batch primitives (Infinite-Canvas parity Phase 1 G3): pure helpers
// ported from Infinite smart-canvas.js — counter-variable injection
// (smartLoopPrompt, :12193), round-index expansion (:14051) and rotating
// prompt selection (smartLoopSelectedLocalPrompt, :12163). Batch-size /
// image-input coupling is deliberately deferred until smart mode grows
// image generation (Phase 2) — the counter stride is always 1 here.

export const MAX_LOOP_ROUNDS = 100;

export function clampRounds(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.max(1, Math.min(MAX_LOOP_ROUNDS, Math.floor(value)));
}

export function clampRoundStart(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.max(1, Math.floor(value));
}

/**
 * Replace the Infinite counter tokens in a prompt text. Full-width tokens
 * are canonical; square-bracket variants are accepted, matching Infinite.
 */
export function injectLoopVariables(
  text: string,
  ctx: { index: number; total: number },
): string {
  return String(text ?? '')
    .replaceAll('《计数》', String(ctx.index))
    .replaceAll('[计数]', String(ctx.index))
    .replaceAll('《总数》', String(ctx.total))
    .replaceAll('[总数]', String(ctx.total))
    .replaceAll('《进度》', `${ctx.index}/${ctx.total}`)
    .replaceAll('[进度]', `${ctx.index}/${ctx.total}`)
    .trim();
}

/** Round indexes for a run: roundStart, roundStart+1, … (stride 1). */
export function loopRoundIndexes(args: { rounds: number; roundStart: number }): number[] {
  const rounds = clampRounds(args.rounds);
  const start = clampRoundStart(args.roundStart);
  return Array.from({ length: rounds }, (_v, i) => start + i);
}

/**
 * Rotating prompt selection: round N uses entry (N-1) % count, over the
 * non-blank entries (Infinite trims and skips empties the same way).
 */
export function pickRotatingPrompt(prompts: readonly string[], index: number): string {
  const values = prompts.map((p) => p.trim()).filter(Boolean);
  if (values.length === 0) return '';
  const i = Math.max(1, Math.floor(index) || 1);
  return values[(i - 1) % values.length];
}
