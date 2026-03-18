import React, { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Lock } from 'lucide-react';
import NodeActionToolbar from '../../canvas/NodeActionToolbar';

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
  imageUrl?: string;
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
  imageUrl,
}: NodeWrapperProps) {
  const [hovered, setHovered] = useState(false);

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
      {/* Node Action Toolbar (appears on select) */}
      <NodeActionToolbar
        nodeId={nodeId}
        isVisible={selected || hovered}
        locked={locked}
        hasImage={Boolean(imageUrl)}
        imageUrl={imageUrl}
      />

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
