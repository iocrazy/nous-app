import React, { useCallback, useEffect, useRef } from 'react';
import {
  Copy,
  ClipboardPaste,
  Trash2,
  Lock,
  Unlock,
  Maximize2,
  Plus,
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

export interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  /** The node ID under the cursor, or null for canvas background */
  targetNodeId: string | null;
}

interface CanvasContextMenuProps {
  menu: ContextMenuState;
  onClose: () => void;
  /** Flow-space position corresponding to the right-click */
  flowPosition: { x: number; y: number };
  onFitView: () => void;
  onPaste: () => void;
  canPaste: boolean;
}

// ─── Node type sub-menu entries ───────────────────────────────────────────────

interface NodeTypeEntry {
  type: StoryboardNode['node_type'];
  label: string;
  icon: React.ReactNode;
}

const ADD_NODE_TYPES: NodeTypeEntry[] = [
  { type: 'upload', label: 'Upload Image', icon: <Upload size={14} /> },
  { type: 'image_edit', label: 'Image Edit', icon: <ImageIcon size={14} /> },
  { type: 'storyboard_split', label: 'Split Frames', icon: <Scissors size={14} /> },
  { type: 'storyboard_gen', label: 'Generate', icon: <Layers size={14} /> },
  { type: 'text_annotation', label: 'Annotation', icon: <Type size={14} /> },
  { type: 'group', label: 'Group', icon: <Square size={14} /> },
  { type: 'image_to_video', label: 'To Video', icon: <Video size={14} /> },
  { type: 'export', label: 'Export', icon: <FileOutput size={14} /> },
];

// ─── Component ────────────────────────────────────────────────────────────────

const CanvasContextMenu = React.memo(function CanvasContextMenu({
  menu,
  onClose,
  flowPosition,
  onFitView,
  onPaste,
  canPaste,
}: CanvasContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const {
    nodes,
    deleteNode,
    duplicateNode,
    updateNodeData,
    addNode,
    pushHistory,
    currentProjectId,
  } = useStoryboardStore();

  // Close on outside click
  useEffect(() => {
    if (!menu.visible) return;

    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    const handleScroll = () => onClose();

    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('scroll', handleScroll, true);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [menu.visible, onClose]);

  const targetNode = menu.targetNodeId
    ? nodes.find((n) => n.id === menu.targetNodeId)
    : null;

  // ─── Node context actions ─────────────────────────────────────────────

  const handleCopyNode = useCallback(() => {
    if (!menu.targetNodeId) return;
    pushHistory();
    duplicateNode(menu.targetNodeId);
    onClose();
  }, [menu.targetNodeId, pushHistory, duplicateNode, onClose]);

  const handleDeleteNode = useCallback(() => {
    if (!menu.targetNodeId) return;
    pushHistory();
    deleteNode(menu.targetNodeId);
    onClose();
  }, [menu.targetNodeId, pushHistory, deleteNode, onClose]);

  const handleToggleLock = useCallback(() => {
    if (!targetNode) return;
    updateNodeData(targetNode.id, { locked: !targetNode.locked });
    onClose();
  }, [targetNode, updateNodeData, onClose]);

  // ─── Canvas background actions ────────────────────────────────────────

  const handleAddNode = useCallback(
    (type: StoryboardNode['node_type']) => {
      const newNode: StoryboardNode = {
        id: `node-${Date.now()}`,
        project_id: currentProjectId ?? '',
        node_type: type,
        position_x: flowPosition.x,
        position_y: flowPosition.y,
        data_json: {},
        sort_order: nodes.length,
        locked: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      pushHistory();
      addNode(newNode);
      onClose();
    },
    [flowPosition, nodes.length, currentProjectId, pushHistory, addNode, onClose],
  );

  const handleFitView = useCallback(() => {
    onFitView();
    onClose();
  }, [onFitView, onClose]);

  const handlePaste = useCallback(() => {
    onPaste();
    onClose();
  }, [onPaste, onClose]);

  if (!menu.visible) return null;

  // ─── Render ─────────────────────────────────────────────────────────────

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 min-w-[160px]"
      style={{ left: menu.x, top: menu.y }}
    >
      {menu.targetNodeId && targetNode ? (
        /* ── Node context menu ─────────────────────────────────────────── */
        <>
          <MenuItem icon={<Copy size={14} />} label="Duplicate" onClick={handleCopyNode} />
          <MenuItem
            icon={targetNode.locked ? <Unlock size={14} /> : <Lock size={14} />}
            label={targetNode.locked ? 'Unlock' : 'Lock'}
            onClick={handleToggleLock}
          />
          <MenuDivider />
          <MenuItem
            icon={<Trash2 size={14} />}
            label="Delete"
            onClick={handleDeleteNode}
            danger
          />
        </>
      ) : (
        /* ── Canvas background context menu ────────────────────────────── */
        <>
          <MenuSection label="Add Node">
            {ADD_NODE_TYPES.map((entry) => (
              <MenuItem
                key={entry.type}
                icon={entry.icon}
                label={entry.label}
                onClick={() => handleAddNode(entry.type)}
              />
            ))}
          </MenuSection>
          <MenuDivider />
          <MenuItem
            icon={<ClipboardPaste size={14} />}
            label="Paste"
            onClick={handlePaste}
            disabled={!canPaste}
          />
          <MenuItem
            icon={<Maximize2 size={14} />}
            label="Fit View"
            onClick={handleFitView}
          />
        </>
      )}
    </div>
  );
});

// ─── Sub-components ───────────────────────────────────────────────────────────

interface MenuItemProps {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}

function MenuItem({ icon, label, onClick, disabled = false, danger = false }: MenuItemProps) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={[
        'px-3 py-1.5 text-sm w-full flex items-center gap-2 transition-colors text-left',
        disabled
          ? 'text-gray-600 cursor-not-allowed'
          : danger
            ? 'text-red-400 hover:bg-red-500/15 cursor-pointer'
            : 'text-gray-200 hover:bg-gray-700 cursor-pointer',
      ].join(' ')}
    >
      <span className={disabled ? 'text-gray-600' : danger ? 'text-red-400' : 'text-gray-400'}>
        {icon}
      </span>
      {label}
    </button>
  );
}

function MenuDivider() {
  return <div className="h-px bg-gray-700 my-1" />;
}

function MenuSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="px-3 py-1 text-xs text-gray-500 font-medium uppercase tracking-wide">
        {label}
      </p>
      {children}
    </div>
  );
}

export default CanvasContextMenu;
