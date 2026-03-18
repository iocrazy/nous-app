import React, { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Trash2, Lock, Copy } from 'lucide-react';
import { useStoryboardStore } from '../../../../stores/storyboardStore';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface HandleConfig {
  type: 'source' | 'target';
  position: Position;
  id?: string;
}

interface NodeWrapperProps {
  nodeId: string;
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  selected?: boolean;
  locked?: boolean;
  handles?: HandleConfig[];
  accentColor?: string;
}

// ─── Default handles ──────────────────────────────────────────────────────────

const DEFAULT_HANDLES: HandleConfig[] = [
  { type: 'target', position: Position.Left },
  { type: 'source', position: Position.Right },
];

// ─── Component ────────────────────────────────────────────────────────────────

const NodeWrapper = React.memo(function NodeWrapper({
  nodeId,
  title,
  icon,
  children,
  selected = false,
  locked = false,
  handles = DEFAULT_HANDLES,
  accentColor = '#3b82f6',
}: NodeWrapperProps) {
  const [hovered, setHovered] = useState(false);
  const { deleteNode, pushHistory, addNode, nodes, currentProjectId } = useStoryboardStore();

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    pushHistory();
    deleteNode(nodeId);
  };

  const handleDuplicate = (e: React.MouseEvent) => {
    e.stopPropagation();
    const original = nodes.find((n) => n.id === nodeId);
    if (!original) return;
    pushHistory();
    addNode({
      ...original,
      id: `node-${Date.now()}`,
      position_x: original.position_x + 30,
      position_y: original.position_y + 30,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  };

  return (
    <div
      className={[
        'relative bg-gray-800 rounded-xl shadow-xl border transition-all duration-150',
        'min-w-[220px] max-w-[320px]',
        selected ? 'border-indigo-500 shadow-indigo-500/20 shadow-lg' : 'border-gray-700',
        hovered && !selected ? 'border-gray-600' : '',
      ].join(' ')}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Accent bar */}
      <div
        className="absolute top-0 left-0 right-0 h-0.5 rounded-t-xl"
        style={{ backgroundColor: accentColor }}
      />

      {/* Header */}
      <div className="flex items-center gap-2 px-3 pt-3 pb-2 border-b border-gray-700/50">
        <span style={{ color: accentColor }}>{icon}</span>
        <span className="flex-1 text-sm font-semibold text-gray-100 truncate">{title}</span>
        {locked && <Lock size={12} className="text-gray-500" />}
      </div>

      {/* Body */}
      <div
        className={[
          'p-3 space-y-2',
          locked ? 'opacity-60 pointer-events-none' : '',
        ].join(' ')}
      >
        {children}
      </div>

      {/* Floating toolbar on hover */}
      {hovered && !locked && (
        <div className="absolute -top-9 left-1/2 -translate-x-1/2 flex items-center gap-1 bg-gray-800 border border-gray-700 rounded-lg px-1.5 py-1 shadow-xl z-10">
          <button
            onClick={handleDuplicate}
            className="p-1 text-gray-400 hover:text-white transition-colors rounded"
            title="Duplicate node"
          >
            <Copy size={13} />
          </button>
          <button
            onClick={handleDelete}
            className="p-1 text-gray-400 hover:text-red-400 transition-colors rounded"
            title="Delete node"
          >
            <Trash2 size={13} />
          </button>
        </div>
      )}

      {/* Handles */}
      {handles.map((h, idx) => (
        <Handle
          key={`${h.type}-${h.position}-${idx}`}
          type={h.type}
          position={h.position}
          id={h.id}
          className="!w-3 !h-3 !bg-gray-600 !border-2 !border-gray-400 hover:!bg-indigo-500 hover:!border-indigo-400 transition-colors"
        />
      ))}
    </div>
  );
});

export default NodeWrapper;
