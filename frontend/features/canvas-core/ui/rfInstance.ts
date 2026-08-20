// features/canvas-core/ui/rfInstance.ts
//
// Module-level handle to the live React Flow instance (one canvas page at
// a time) so window-level shortcut handlers (Z overview) can drive the
// viewport without threading the instance through every hook.

import type { ReactFlowInstance } from '@xyflow/react';

let inst: ReactFlowInstance | null = null;

export function setRfInstance(i: ReactFlowInstance | null): void {
  inst = i;
}

export function getRfInstance(): ReactFlowInstance | null {
  return inst;
}
