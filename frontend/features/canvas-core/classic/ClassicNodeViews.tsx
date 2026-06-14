/**
 * ClassicMode node views — PLACEHOLDER skeleton (Phase 5a B2).
 *
 * B4 will replace these placeholder views with the real MVP node UIs
 * (typed-port editors, run state, ComfyUI graph embed, etc.). For now
 * each view is a tiny `ink-*` styled card that renders the node label and
 * a React Flow <Handle> per typed port, so the CanvasSurface dispatch
 * compiles and ClassicMode renders SOMETHING without affecting SmartMode.
 *
 * // B4 will replace these placeholder views
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import {
  classicNodeDefinitions,
  type ClassicNodeDefinition,
  type ClassicPortType,
} from './registry';

const PORT_TONE: Record<ClassicPortType, string> = {
  image: '!bg-emerald-400',
  text: '!bg-sky-400',
  prompt: '!bg-amber-400',
};

function makeClassicPlaceholderView(def: ClassicNodeDefinition) {
  function ClassicPlaceholderView({ selected }: NodeProps) {
    return (
      <div
        data-testid={`classic-node-${def.type}`}
        className={`min-w-[140px] rounded-md border bg-ink-900 px-3 py-2 text-ink-100 shadow ${
          selected ? 'border-ink-400' : 'border-ink-700'
        }`}
      >
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-300">
          {def.label}
        </div>
        {/* Input handles on the left, output handles on the right. Spread
            them vertically so multiple typed ports don't overlap. */}
        {def.inputs.map((port, idx) => (
          <Handle
            key={`in-${port.id}`}
            id={port.id}
            type="target"
            position={Position.Left}
            className={`!h-2 !w-2 ${PORT_TONE[port.type]}`}
            style={{ top: 28 + idx * 14 }}
          />
        ))}
        {def.outputs.map((port, idx) => (
          <Handle
            key={`out-${port.id}`}
            id={port.id}
            type="source"
            position={Position.Right}
            className={`!h-2 !w-2 ${PORT_TONE[port.type]}`}
            style={{ top: 28 + idx * 14 }}
          />
        ))}
      </div>
    );
  }
  ClassicPlaceholderView.displayName = `ClassicPlaceholderView(${def.type})`;
  return ClassicPlaceholderView;
}

/**
 * React Flow `nodeTypes` map for ClassicMode — passed to
 * `<ReactFlow nodeTypes={...} />`. Built once at module scope so React
 * Flow doesn't recreate its node renderers on every render.
 */
export const CLASSIC_NODE_TYPES = Object.fromEntries(
  Object.values(classicNodeDefinitions).map((def) => [def.type, makeClassicPlaceholderView(def)]),
) as Record<string, ReturnType<typeof makeClassicPlaceholderView>>;
