// features/canvas-core/smart/edgeRunState.ts
//
// Run-state edge colouring (Infinite-Canvas parity Phase 1 G2): a cascade
// run should visibly flow along the wires (Infinite smart-canvas runPath —
// wait / active / done edge states + dash-flow animation). MediaHub derives
// the state from the adjacent prompt's persisted run_status instead of a
// transient run-session map, so the visual survives reloads and matches the
// node halos (RUN_STATUS_TONE) for free.
//
// Rule: an edge shows the status of its adjacent prompt — the TARGET prompt
// wins (the wire feeds it); otherwise the SOURCE prompt (prompt→output /
// prompt→loop wires carry their producer's state). Non-prompt-adjacent
// edges (e.g. shot→loop pass-through) stay undecorated until G3 gives loops
// their own run semantics.

export type EdgeRunClass =
  | 'mh-edge-wait'
  | 'mh-edge-active'
  | 'mh-edge-done'
  | 'mh-edge-failed'
  | 'mh-edge-blocked';

const STATUS_CLASS: Record<string, EdgeRunClass> = {
  queued: 'mh-edge-wait',
  running: 'mh-edge-active',
  succeeded: 'mh-edge-done',
  failed: 'mh-edge-failed',
  blocked: 'mh-edge-blocked',
};

export interface EdgeRunNodeInfo {
  type?: string;
  runStatus?: string;
}

export function edgeRunStateClass(
  edge: { source: string; target: string },
  nodeInfo: (id: string) => EdgeRunNodeInfo | undefined,
): EdgeRunClass | null {
  const target = nodeInfo(edge.target);
  if (target?.type === 'prompt' && target.runStatus) {
    return STATUS_CLASS[target.runStatus] ?? null;
  }
  const source = nodeInfo(edge.source);
  if (source?.type === 'prompt' && source.runStatus) {
    return STATUS_CLASS[source.runStatus] ?? null;
  }
  return null;
}
