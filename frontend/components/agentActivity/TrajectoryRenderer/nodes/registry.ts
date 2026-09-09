/**
 * Trajectory node registry (seam C, render half). A node kind renders through
 * the component registered for it; a new kind = a new `register` call, never
 * a branch in the renderer. Enumerable so a test can assert every folded
 * kind has a renderer — an unregistered kind renders nothing and is a bug the
 * test catches, not a silent hole in the timeline.
 */

import type { ComponentType } from 'react';

import type { TrajectoryNode } from '../foldEvents';

export interface NodeProps<N extends TrajectoryNode = TrajectoryNode> {
  node: N;
  /** Steps the user opened by hand; the live step is open regardless. */
  expanded: boolean;
  onToggle?: (node: N) => void;
  /** Phase 2b-1 §2: runs forked at this node (run ids) — a step boundary. */
  marks?: string[];
}

type AnyNodeComponent = ComponentType<NodeProps<TrajectoryNode>>;

const registry = new Map<TrajectoryNode['kind'], AnyNodeComponent>();

export function registerTrajectoryNode<K extends TrajectoryNode['kind']>(
  kind: K,
  component: ComponentType<NodeProps<Extract<TrajectoryNode, { kind: K }>>>,
): void {
  if (registry.has(kind)) throw new Error(`trajectory node "${kind}" already registered`);
  registry.set(kind, component as unknown as AnyNodeComponent);
}

export function trajectoryNodeFor(kind: TrajectoryNode['kind']): AnyNodeComponent | null {
  return registry.get(kind) ?? null;
}

export function registeredTrajectoryNodeKinds(): TrajectoryNode['kind'][] {
  return [...registry.keys()].sort();
}

/** Tests only. */
export function __resetTrajectoryNodeRegistry(): void {
  registry.clear();
}
