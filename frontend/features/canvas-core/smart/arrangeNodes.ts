// features/canvas-core/smart/arrangeNodes.ts
//
// Auto-layout (IC's 整理选中 / arrangeSelectedSmartNodes), shared by the
// composer Arrange button and the floating minimap Arrange button.
//
// - With 2+ top-level nodes selected: lay out only that subset in place
//   (dagre re-centres on the subset's own centroid) and leave the rest.
// - Otherwise: lay out every top-level node.
// Group CHILDREN carry parent-relative positions — dagre must never touch
// them (they'd teleport), so they are spliced back untouched.

import { arrangeLayout } from '../../../canvas-kit/arrangeLayout';
import type { CanvasConnection, CanvasNode } from '../types';

const asObj = (n: unknown) => n as Record<string, unknown>;
const idOf = (n: unknown) => asObj(n).id as string;
const isChild = (n: unknown) => typeof asObj(n).parentId === 'string';

/**
 * Returns the re-laid-out node array, or null when there's nothing to do
 * (fewer than 2 nodes in scope). Pure — callers commit via setNodes.
 */
export function arrangeSelected(
  nodes: CanvasNode[],
  connections: CanvasConnection[],
  selection: string[],
): CanvasNode[] | null {
  const topLevel = nodes.filter((n) => !isChild(n));
  const children = nodes.filter(isChild);

  const sel = new Set(selection);
  const scoped =
    selection.length >= 2
      ? topLevel.filter((n) => sel.has(idOf(n)))
      : topLevel;
  if (scoped.length < 2) return null;

  const scopedIds = new Set(scoped.map(idOf));
  const rest = topLevel.filter((n) => !scopedIds.has(idOf(n)));
  return [
    ...rest,
    ...arrangeLayout(
      scoped as Parameters<typeof arrangeLayout>[0],
      connections as Parameters<typeof arrangeLayout>[1],
    ),
    ...children,
  ];
}

/** True when the floating Arrange affordance should show: a real subset
 *  (2+ top-level nodes) is selected. */
export function canArrangeSelection(
  nodes: CanvasNode[],
  selection: string[],
): boolean {
  if (selection.length < 2) return false;
  const sel = new Set(selection);
  const scoped = nodes.filter((n) => !isChild(n) && sel.has(idOf(n)));
  return scoped.length >= 2;
}
