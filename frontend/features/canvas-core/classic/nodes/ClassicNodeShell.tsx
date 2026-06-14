/**
 * Shared ClassicMode node shell (Phase 5a B4).
 *
 * Every classic node renders the SAME chrome:
 *   - a run-state HALO keyed off `run_status` via `RUN_STATUS_TONE`
 *     (reusing SmartMode's exact tone vocabulary — see PromptNodeView),
 *   - one React Flow <Handle> per typed port from the registry, the
 *     handle `id` set to the registry port id, tinted by port type, with
 *     a small text label, and
 *   - inline `run_error` text (rose) when the node failed.
 *
 * Node-specific UI (e.g. the comfy timer + Cancel button) is passed as
 * `children` and rendered below the port region.
 */

import { Handle, Position } from '@xyflow/react';
import type { ReactNode } from 'react';

import { RUN_STATUS_TONE } from '../../smart/types';
import type { ClassicNodeDefinition, ClassicPort } from '../registry';
import { CLASSIC_PORT_TONE, PORT_HEADER_OFFSET, PORT_ROW_GAP } from './portTone';

/** One run_status value, reusing the SmartMode union shape. */
export type ClassicRunStatus = keyof typeof RUN_STATUS_TONE;

function PortEntry({
  port,
  idx,
  side,
}: {
  port: ClassicPort;
  idx: number;
  side: 'input' | 'output';
}) {
  const top = PORT_HEADER_OFFSET + idx * PORT_ROW_GAP;
  const isInput = side === 'input';
  return (
    <>
      <Handle
        id={port.id}
        type={isInput ? 'target' : 'source'}
        position={isInput ? Position.Left : Position.Right}
        className={`!h-3 !w-3 !border !border-ink-950 ${CLASSIC_PORT_TONE[port.type]}`}
        style={{ top }}
      />
      <span
        className={`pointer-events-none absolute text-[10px] text-ink-300 ${
          isInput ? 'left-3' : 'right-3'
        }`}
        style={{ top: top - 6 }}
      >
        {port.type}
      </span>
    </>
  );
}

export function ClassicNodeShell({
  def,
  runStatus,
  runError,
  selected,
  children,
}: {
  def: ClassicNodeDefinition;
  runStatus: ClassicRunStatus;
  runError: string | null;
  selected: boolean;
  children?: ReactNode;
}) {
  // Reuse the SmartMode halo rule: selected wins, else the run-state tone.
  const haloTone = selected ? 'border-indigo-500' : RUN_STATUS_TONE[runStatus];
  const portRows = Math.max(def.inputs.length, def.outputs.length);
  const portRegionHeight = portRows > 0 ? PORT_HEADER_OFFSET + portRows * PORT_ROW_GAP : 8;

  return (
    <div
      data-testid={`classic-node-${def.type}`}
      className={`relative min-w-[170px] rounded-md border-2 bg-ink-900 text-ink-100 shadow ${haloTone}`}
    >
      <div className="flex items-center justify-between border-b border-ink-700 px-3 py-1.5">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-300">
          {def.label}
        </div>
        <div
          className="text-[10px] uppercase tracking-wider text-ink-400"
          data-testid={`classic-node-${def.type}-status`}
        >
          {runStatus}
        </div>
      </div>

      {/* Port region — handles are absolutely positioned at the node edge,
          their labels sit just inside. */}
      <div className="relative px-3" style={{ minHeight: portRegionHeight }}>
        {def.inputs.map((port, idx) => (
          <PortEntry key={`in-${port.id}`} port={port} idx={idx} side="input" />
        ))}
        {def.outputs.map((port, idx) => (
          <PortEntry key={`out-${port.id}`} port={port} idx={idx} side="output" />
        ))}
      </div>

      {(children || runError) && (
        <div className="border-t border-ink-800 px-3 py-2">
          {children}
          {runError && (
            <div
              className="mt-1 truncate text-[11px] text-rose-600 dark:text-rose-400"
              title={runError}
              data-testid={`classic-node-${def.type}-error`}
            >
              {runError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
