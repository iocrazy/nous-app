import React, { useCallback } from 'react';
import {
  Upload,
  ImageIcon,
  Scissors,
  Layers,
  Type,
  Square,
  Video,
  FileOutput,
} from 'lucide-react';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { StoryboardNode } from '../../../types';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface NodeSelectionMenuProps {
  visible: boolean;
  position: { x: number; y: number };
  connectingNodeId: string | null;
  onClose: () => void;
}

interface NodeTypeEntry {
  type: StoryboardNode['node_type'];
  label: string;
  icon: React.ReactNode;
  color: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const NODE_TYPES: NodeTypeEntry[] = [
  { type: 'upload', label: 'Upload', icon: <Upload size={16} />, color: 'text-blue-400' },
  { type: 'image_edit', label: 'Edit Image', icon: <ImageIcon size={16} />, color: 'text-purple-400' },
  { type: 'storyboard_split', label: 'Split Frames', icon: <Scissors size={16} />, color: 'text-yellow-400' },
  { type: 'storyboard_gen', label: 'Generate', icon: <Layers size={16} />, color: 'text-green-400' },
  { type: 'text_annotation', label: 'Annotation', icon: <Type size={16} />, color: 'text-gray-400' },
  { type: 'group', label: 'Group', icon: <Square size={16} />, color: 'text-gray-500' },
  { type: 'image_to_video', label: 'To Video', icon: <Video size={16} />, color: 'text-pink-400' },
  { type: 'export', label: 'Export', icon: <FileOutput size={16} />, color: 'text-red-400' },
];

// ─── Component ────────────────────────────────────────────────────────────────

const NodeSelectionMenu = React.memo(function NodeSelectionMenu({
  visible,
  position,
  connectingNodeId,
  onClose,
}: NodeSelectionMenuProps) {
  const { addNode, setEdges, edges, nodes, currentProjectId } = useStoryboardStore();

  const handleSelectType = useCallback(
    (type: StoryboardNode['node_type']) => {
      const newNode: StoryboardNode = {
        id: `node-${Date.now()}`,
        project_id: currentProjectId ?? '',
        node_type: type,
        position_x: position.x,
        position_y: position.y,
        data_json: {},
        sort_order: nodes.length,
        locked: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      addNode(newNode);

      // Auto-connect if we have a source node
      if (connectingNodeId) {
        const newEdge = {
          id: `edge-${connectingNodeId}-${newNode.id}-${Date.now()}`,
          project_id: currentProjectId ?? '',
          source_node_id: connectingNodeId,
          target_node_id: newNode.id,
          edge_type: 'default',
          created_at: new Date().toISOString(),
        };
        setEdges([...edges, newEdge]);
      }

      onClose();
    },
    [position, nodes.length, currentProjectId, addNode, connectingNodeId, edges, setEdges, onClose]
  );

  if (!visible) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40"
        onClick={onClose}
      />
      {/* Menu */}
      <div
        className="absolute z-50 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl p-2 min-w-[200px]"
        style={{ left: position.x, top: position.y }}
      >
        <p className="text-xs text-gray-500 px-2 pb-2 font-medium uppercase tracking-wide">
          Add Node
        </p>
        <div className="grid grid-cols-2 gap-1">
          {NODE_TYPES.map((entry) => (
            <button
              key={entry.type}
              onClick={() => handleSelectType(entry.type)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-200 hover:bg-gray-800 transition-colors text-left"
            >
              <span className={entry.color}>{entry.icon}</span>
              <span className="truncate">{entry.label}</span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
});

export default NodeSelectionMenu;
