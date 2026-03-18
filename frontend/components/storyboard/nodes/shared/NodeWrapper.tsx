import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Lock, Edit2, Check } from 'lucide-react';
import NodeActionToolbar from '../../canvas/NodeActionToolbar';
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
  imageUrl?: string;
  /** Allow inline title editing. When true, the title is editable and saved to data_json.label. */
  editableTitle?: boolean;
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
  editableTitle = true,
}: NodeWrapperProps) {
  const [hovered, setHovered] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);
  const { updateNodeData, nodes } = useStoryboardStore();

  // Focus input when entering edit mode
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const handleStartEdit = useCallback(() => {
    if (locked || !editableTitle) return;
    setEditValue(title);
    setEditing(true);
  }, [locked, editableTitle, title]);

  const handleSave = useCallback(() => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== title) {
      const node = nodes.find((n) => n.id === nodeId);
      const currentData = (node?.data_json ?? {}) as Record<string, unknown>;
      updateNodeData(nodeId, {
        data_json: { ...currentData, label: trimmed },
      });
    }
    setEditing(false);
  }, [editValue, title, nodeId, nodes, updateNodeData]);

  const handleCancel = useCallback(() => {
    setEditValue(title);
    setEditing(false);
  }, [title]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') handleSave();
      if (e.key === 'Escape') handleCancel();
    },
    [handleSave, handleCancel]
  );

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

        {editing ? (
          <input
            ref={inputRef}
            className="flex-1 bg-transparent text-sm font-semibold text-gray-100 focus:outline-none border-b border-blue-500 min-w-0"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={handleSave}
            onKeyDown={handleKeyDown}
          />
        ) : (
          <span
            className="flex-1 text-sm font-semibold text-gray-100 truncate"
            onDoubleClick={handleStartEdit}
          >
            {title}
          </span>
        )}

        {editableTitle && !locked && (hovered || selected) && !editing && (
          <button
            onClick={handleStartEdit}
            className="p-0.5 text-gray-500 hover:text-gray-300 transition-colors"
            title="Edit title"
          >
            <Edit2 size={11} />
          </button>
        )}

        {editing && (
          <button
            onClick={handleSave}
            className="p-0.5 text-blue-400 hover:text-blue-300 transition-colors"
            title="Save title"
          >
            <Check size={12} />
          </button>
        )}

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
