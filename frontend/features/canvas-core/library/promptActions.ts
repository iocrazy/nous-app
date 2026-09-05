// features/canvas-core/library/promptActions.ts
//
// The pure half of the Prompts page's actions (spec §3.4 table). No store, no
// editor: LibraryPromptsPage applies these through patchNode/setNodes and the
// mention-handle registry, and the tests here never need React.
import { RATIO_LABELS } from '../smart/nodes/GenFooterControls';
import type { PromptNodeData } from '../smart/types';
import { ratioFromParams, type PromptEntry } from '../../../services/promptsService';

export function appendPositive(body: string, positive: string): string {
  const base = body.replace(/\s+$/, '');
  return base ? `${base}\n${positive}` : positive;
}

/** A picture's size as one of the node's ratio presets, or null. */
export function ratioPreset(params: Record<string, unknown> | null): string | null {
  const ratio = ratioFromParams(params);
  return ratio && Object.prototype.hasOwnProperty.call(RATIO_LABELS, ratio) ? ratio : null;
}

export function buildApplyAllPatch(args: {
  positive: string;
  negative: string | null;
  params: Record<string, unknown> | null;
  node: PromptNodeData;
}): Partial<PromptNodeData> {
  const patch: Partial<PromptNodeData> = { body: args.positive, negative_body: args.negative ?? '' };
  const ratio = ratioPreset(args.params);
  if (ratio && args.node.gen?.kind === 'image') {
    patch.gen = { ...args.node.gen, ratio };
  }
  return patch;
}

export function groupChips(items: PromptEntry[], max = 8): string[] {
  const freq = new Map<string, number>();
  for (const it of items) for (const tag of it.tags ?? []) freq.set(tag, (freq.get(tag) ?? 0) + 1);
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, max)
    .map(([tag]) => tag);
}
