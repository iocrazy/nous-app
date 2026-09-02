/**
 * Load-time self-heal for stale generation-slot shimmer cells.
 *
 * `gen_pending` counts shimmer cells that some transient owner promises to
 * settle: the live runner (onItemSettled), genResume (tasks still in the
 * prompt's `gen_tasks` registry), or the recover overlay's re-query. All of
 * those live in memory; the count lives in nodes_json. When a canvas loads
 * with a pending count but NO owner — prompt terminal, registry empty —
 * nothing will ever decrement it, and the cell pulses forever (reported
 * 2026-09-02 with a screenshot: one delivered image + one eternal shimmer).
 *
 * Heals the class at load, beside the 2026-08-12 duplicate-node dedupe.
 * Recover entries are deliberately untouched: "task not lost" stays valid
 * indefinitely and carries its own re-query UI.
 */
import type { CanvasNode } from '../types';

type AnyNode = { id?: unknown; data?: Record<string, unknown> };

/** Local copy of genSlots' tag reader. Deliberately NOT imported: genSlots
 *  pulls in the store, and this module runs INSIDE the store's load path —
 *  importing it is a module cycle that broke 40 store tests when tried. */
function genSlotTagOf(node: unknown): { node_id?: string } | undefined {
  const data = asObj(node).data;
  const tag = data?.gen_slot;
  return tag && typeof tag === 'object' ? (tag as { node_id?: string }) : undefined;
}

const asObj = (n: unknown): AnyNode => (n ?? {}) as AnyNode;

/** Does anything still own this slot's pending cells? */
function hasLiveOwner(promptData: Record<string, unknown> | undefined): boolean {
  if (!promptData) return false; // orphan slot: no prompt, no owner, ever slot: no prompt, no owner, ever
  const status = promptData.run_status;
  if (status === 'running' || status === 'queued') return true;
  const registry = promptData.gen_tasks;
  return Array.isArray(registry) && registry.length > 0;
}

/** Clamp ownerless `gen_pending` to 0. Returns the SAME array when nothing
 *  needs healing — load runs this on every canvas and a no-op must not churn
 *  node identity. */
export function healStaleGenSlots(nodes: CanvasNode[]): CanvasNode[] {
  let touched = false;
  const healed = nodes.map((node) => {
    const tag = genSlotTagOf(node);
    if (!tag) return node;
    const data = asObj(node).data ?? {};
    const pending = typeof data.gen_pending === 'number' ? data.gen_pending : 0;
    if (pending <= 0) return node;
    const prompt = nodes.find((n) => asObj(n).id === tag.node_id);
    if (hasLiveOwner(asObj(prompt).data)) return node;
    touched = true;
    return {
      ...(node as object),
      data: { ...data, gen_pending: 0 },
    } as CanvasNode;
  });
  return touched ? healed : nodes;
}
