/**
 * Smart-mode prompt runner (Phase 2 Day 4-5).
 *
 * Owns the lifecycle of a Run / Cascade Run from the UI perspective:
 *   1. Move the target prompts through idle → queued → running →
 *      succeeded | failed.
 *   2. Stamp run_started_at / run_finished_at / run_error.
 *
 * This PR ships a MOCK implementation (`mockRunner`) that just sleeps
 * and resolves — the real provider adapter (Jimeng CLI / nous-center)
 * lands in a follow-up. The runner is parameterised on the prompt-call
 * function so the swap is mechanical: pass `realRunner` instead of
 * `mockRunner` to `runPrompts` and nothing else changes.
 */

import type { PromptNodeData } from './types';

export type PromptStatus = PromptNodeData['run_status'];

export interface RunnerContext {
  promptId: string;
  body: string;
  provider_slug: string;
  agent_id: string | null;
}

export interface RunnerResult {
  ok: boolean;
  /** Free-form result text — surfaces in the downstream output node's
   *  preview_text in a future slice. Empty for failures. */
  text: string;
  /** When ok=false. */
  error: string | null;
}

export type PromptCaller = (ctx: RunnerContext) => Promise<RunnerResult>;

export interface RunHandlers {
  onStatusChange(promptId: string, next: PromptStatus, fields: {
    run_started_at?: string | null;
    run_finished_at?: string | null;
    run_error?: string | null;
  }): void;
  onResult?(promptId: string, result: RunnerResult): void;
  /** Defaults to `() => new Date().toISOString()`. Pinned by tests. */
  now?(): string;
}

const DEFAULT_NOW = (): string => new Date().toISOString();

/** Mock runner — sleeps 0-30ms, succeeds with "mock" text. */
export const mockRunner: PromptCaller = async (ctx) => {
  await new Promise((r) => setTimeout(r, 5));
  return {
    ok: true,
    text: `mock(${ctx.promptId})`,
    error: null,
  };
};

/** Always-failing runner — used by tests to assert the failure path. */
export const failingMockRunner: PromptCaller = async (ctx) => {
  await new Promise((r) => setTimeout(r, 5));
  return {
    ok: false,
    text: '',
    error: `mock failure for ${ctx.promptId}`,
  };
};

/**
 * Run one prompt. Public surface for the "Run" button.
 *
 * Marks idle → queued → running → done. Always resolves; failures are
 * surfaced via run_status='failed' + run_error, not by throwing, so
 * callers can sequence further work without try/catch boilerplate.
 */
export async function runSinglePrompt(
  ctx: RunnerContext,
  caller: PromptCaller,
  handlers: RunHandlers,
): Promise<RunnerResult> {
  const now = handlers.now ?? DEFAULT_NOW;
  handlers.onStatusChange(ctx.promptId, 'queued', {
    run_started_at: null,
    run_finished_at: null,
    run_error: null,
  });
  handlers.onStatusChange(ctx.promptId, 'running', {
    run_started_at: now(),
  });

  let result: RunnerResult;
  try {
    result = await caller(ctx);
  } catch (err) {
    result = {
      ok: false,
      text: '',
      error: err instanceof Error ? err.message : String(err),
    };
  }

  if (result.ok) {
    handlers.onStatusChange(ctx.promptId, 'succeeded', {
      run_finished_at: now(),
      run_error: null,
    });
  } else {
    handlers.onStatusChange(ctx.promptId, 'failed', {
      run_finished_at: now(),
      run_error: result.error,
    });
  }

  handlers.onResult?.(ctx.promptId, result);
  return result;
}

/**
 * Run a list of prompts in dependency order (caller hands in the
 * already-sorted ids — see `topoSortPrompts`). Stops at the first
 * failure by default; pass `continueOnFailure: true` to soldier on.
 */
export async function runPrompts(
  orderedContexts: RunnerContext[],
  caller: PromptCaller,
  handlers: RunHandlers,
  options: { continueOnFailure?: boolean } = {},
): Promise<RunnerResult[]> {
  const out: RunnerResult[] = [];
  for (const ctx of orderedContexts) {
    const result = await runSinglePrompt(ctx, caller, handlers);
    out.push(result);
    if (!result.ok && !options.continueOnFailure) break;
  }
  return out;
}
