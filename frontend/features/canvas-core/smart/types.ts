/**
 * Smart-mode node-type definitions (Phase 2 of canvas + AI upgrade).
 *
 *   shot    — describes a frame: title + reference image refs + notes
 *   prompt  — the AI generation step that turns one or more shots into
 *             generated content. Owns the run config.
 *   output  — the rendered result of a prompt run (image / video / text).
 *
 * The smart-mode graph topology is shot(s) → prompt → output. The
 * connect rules are enforced by `canConnectSmart` in this module; the
 * UI layer just calls into it.
 */

import type { CanvasNode } from '../types';

export type SmartNodeType = 'shot' | 'prompt' | 'output' | 'loop';

export type LoopMode = 'serial' | 'parallel' | 'batch';

export interface ShotNodeData {
  title: string;
  /** Snowflake resource IDs (strings to avoid bigint precision loss). */
  reference_resource_ids: string[];
  notes: string;
}

export interface PromptNodeData {
  body: string;
  /** Slug of the provider node-pack to invoke. Empty = not yet wired. */
  provider_slug: string;
  /** Snowflake AI library agent ID to ask, or null = use provider default. */
  agent_id: string | null;
  /** "idle" | "queued" | "running" | "succeeded" | "failed" — driven by
   *  the run protocol when it lands. Persisted so a reload shows the
   *  last-known state. */
  run_status: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed';
  run_started_at: string | null;
  run_finished_at: string | null;
  run_error: string | null;
}

export type OutputKind = 'text' | 'image' | 'video' | 'audio';

export interface OutputNodeData {
  kind: OutputKind;
  /** Snowflake resource id that owns the rendered artifact, when one
   *  was persisted. Null for in-flight or text-only outputs. */
  resource_id: string | null;
  /** Inline preview text — used for kind='text' and as a fallback
   *  caption for media. */
  preview_text: string;
}

export interface LoopNodeData {
  mode: LoopMode;
  /** Optional human label, e.g. "for each shot". */
  label: string;
}

export interface SmartNode<T> extends Record<string, unknown> {
  id: string;
  type: SmartNodeType;
  position: { x: number; y: number };
  data: T;
}

export type ShotNode = SmartNode<ShotNodeData>;
export type PromptNode = SmartNode<PromptNodeData>;
export type OutputNode = SmartNode<OutputNodeData>;
export type LoopNode = SmartNode<LoopNodeData>;
export type AnySmartNode = ShotNode | PromptNode | OutputNode | LoopNode;

/**
 * Connect-rule predicate for smart mode.
 *
 *   shot   → prompt          ✓
 *   shot   → loop            ✓ (loop fans out a shot collection)
 *   prompt → output          ✓
 *   prompt → prompt          ✓ (chain: one prompt feeds the next)
 *   prompt → loop            ✓ (prompt output can drive a loop)
 *   loop   → prompt          ✓ (loop iterates a prompt downstream)
 *   loop   → loop            ✓ (nested fanouts)
 *   shot   → output          ✗ (must go through a prompt)
 *   loop   → output          ✗ (always needs a prompt downstream)
 *   loop   → shot            ✗ (shots are sources)
 *   output → anything        ✗ (outputs are terminal)
 *   *      → shot            ✗ (shots are sources only)
 *   <unknown type>           allow — the surface is mode-aware, custom
 *                            future types opt in their own rules.
 */
export function canConnectSmart(
  sourceType: string | undefined,
  targetType: string | undefined,
): boolean {
  if (!sourceType || !targetType) return true;
  if (sourceType === 'output') return false;
  if (targetType === 'shot') return false;
  if (sourceType === 'shot' && targetType !== 'prompt' && targetType !== 'loop')
    return false;
  if (sourceType === 'loop' && (targetType === 'output' || targetType === 'shot'))
    return false;
  return true;
}

/** Default min-width per node type. Used by the renderer + factory. */
export const SMART_NODE_DEFAULT_WIDTH: Record<SmartNodeType, number> = {
  shot: 240,
  prompt: 280,
  output: 260,
  loop: 200,
};

export const LOOP_MODE_TONE: Record<LoopMode, string> = {
  serial: 'border-slate-400 dark:border-slate-600',
  parallel: 'border-violet-500',
  batch: 'border-cyan-500',
};

/** Run-status colour token for the prompt node halo + the output badge. */
export const RUN_STATUS_TONE: Record<PromptNodeData['run_status'], string> = {
  idle: 'border-slate-300 dark:border-slate-700',
  queued: 'border-amber-400',
  running: 'border-indigo-500 animate-pulse',
  succeeded: 'border-emerald-500',
  failed: 'border-rose-500',
};

export function isSmartNode(node: CanvasNode): node is AnySmartNode {
  const obj = node as Record<string, unknown>;
  return (
    typeof obj.id === 'string' &&
    typeof obj.type === 'string' &&
    (obj.type === 'shot' ||
      obj.type === 'prompt' ||
      obj.type === 'output' ||
      obj.type === 'loop')
  );
}
