// features/canvas-core/smart/edges/registry.ts
// Smart-mode edge registry — module-level constant so the reference stays
// stable across renders (same rule as nodes/registry: a fresh object per
// render would defeat React Flow's memoization).

import type { EdgeTypes } from '@xyflow/react';

import { SmartEdgeView } from './SmartEdgeView';

export const SMART_EDGE_TYPES: EdgeTypes = {
  default: SmartEdgeView,
};
