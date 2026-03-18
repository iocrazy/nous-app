import React, { useCallback } from 'react';
import { NodeToolbar, Position } from '@xyflow/react';
import { Copy, Trash2, Download, Lock, Unlock } from 'lucide-react';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── Types ────────────────────────────────────────────────────────────────────

interface NodeActionToolbarProps {
  nodeId: string;
  isVisible: boolean;
  locked?: boolean;
  hasImage?: boolean;
  imageUrl?: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const TOOLBAR_OFFSET = 10;
const BUTTON_CLASS =
  'p-1.5 text-gray-300 hover:text-white transition-colors rounded-full hover:bg-gray-700/80';
const DELETE_BUTTON_CLASS =
  'p-1.5 text-gray-300 hover:text-red-400 transition-colors rounded-full hover:bg-red-500/20';

// ─── Component ────────────────────────────────────────────────────────────────

const NodeActionToolbar = React.memo(function NodeActionToolbar({
  nodeId,
  isVisible,
  locked = false,
  hasImage = false,
  imageUrl,
}: NodeActionToolbarProps) {
  const { deleteNode, duplicateNode, updateNodeData, pushHistory } =
    useStoryboardStore();

  const handleCopy = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      pushHistory();
      duplicateNode(nodeId);
    },
    [nodeId, pushHistory, duplicateNode],
  );

  const handleDelete = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      pushHistory();
      deleteNode(nodeId);
    },
    [nodeId, pushHistory, deleteNode],
  );

  const handleDownload = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!imageUrl) return;

      const link = document.createElement('a');
      link.href = imageUrl;
      link.download = `node-${nodeId}.png`;
      link.target = '_blank';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    },
    [nodeId, imageUrl],
  );

  const handleLockToggle = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      updateNodeData(nodeId, { locked: !locked });
    },
    [nodeId, locked, updateNodeData],
  );

  return (
    <NodeToolbar
      nodeId={nodeId}
      isVisible={isVisible}
      position={Position.Top}
      align="center"
      offset={TOOLBAR_OFFSET}
      className="pointer-events-auto"
    >
      <div className="flex items-center gap-0.5 bg-gray-800/90 backdrop-blur border border-gray-700 rounded-full px-1 py-0.5 shadow-lg">
        {/* Copy / Duplicate */}
        <button
          onClick={handleCopy}
          className={BUTTON_CLASS}
          title="Duplicate node"
        >
          <Copy size={14} />
        </button>

        {/* Download (only if node has an image) */}
        {hasImage && imageUrl && (
          <button
            onClick={handleDownload}
            className={BUTTON_CLASS}
            title="Download image"
          >
            <Download size={14} />
          </button>
        )}

        {/* Lock / Unlock */}
        <button
          onClick={handleLockToggle}
          className={BUTTON_CLASS}
          title={locked ? 'Unlock node' : 'Lock node'}
        >
          {locked ? <Lock size={14} /> : <Unlock size={14} />}
        </button>

        {/* Delete */}
        <button
          onClick={handleDelete}
          className={DELETE_BUTTON_CLASS}
          title="Delete node"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </NodeToolbar>
  );
});

export default NodeActionToolbar;
