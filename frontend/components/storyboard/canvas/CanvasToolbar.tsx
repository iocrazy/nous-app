import React, { useState, useCallback, useRef, useEffect } from 'react';
import { useReactFlow } from '@xyflow/react';
import {
  Plus,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Lock,
  Unlock,
  Undo2,
  Redo2,
  Download,
  Users,
  ChevronDown,
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

interface CanvasToolbarProps {
  locked: boolean;
  onLockToggle: () => void;
  onCharactersToggle: () => void;
}

interface NodeTypeOption {
  type: StoryboardNode['node_type'];
  label: string;
  icon: React.ReactNode;
}

interface ExportOption {
  label: string;
  format: 'png' | 'pdf' | 'zip';
}

// ─── Constants ────────────────────────────────────────────────────────────────

const NODE_TYPE_OPTIONS: NodeTypeOption[] = [
  { type: 'upload', label: 'Upload Image', icon: <Upload size={14} /> },
  { type: 'image_edit', label: 'Image Edit', icon: <ImageIcon size={14} /> },
  { type: 'storyboard_split', label: 'Split Frames', icon: <Scissors size={14} /> },
  { type: 'storyboard_gen', label: 'Generate Storyboard', icon: <Layers size={14} /> },
  { type: 'text_annotation', label: 'Text Annotation', icon: <Type size={14} /> },
  { type: 'group', label: 'Group', icon: <Square size={14} /> },
  { type: 'image_to_video', label: 'Image to Video', icon: <Video size={14} /> },
  { type: 'export', label: 'Export', icon: <FileOutput size={14} /> },
];

const EXPORT_OPTIONS: ExportOption[] = [
  { label: 'Export as PNG', format: 'png' },
  { label: 'Export as PDF', format: 'pdf' },
  { label: 'Export as ZIP', format: 'zip' },
];

// ─── Component ────────────────────────────────────────────────────────────────

const CanvasToolbar = React.memo(function CanvasToolbar({
  locked,
  onLockToggle,
  onCharactersToggle,
}: CanvasToolbarProps) {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { nodes, addNode, undo, redo, history, currentProjectId } = useStoryboardStore();

  const [showAddMenu, setShowAddMenu] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);

  const addMenuRef = useRef<HTMLDivElement>(null);
  const exportMenuRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (addMenuRef.current && !addMenuRef.current.contains(e.target as Node)) {
        setShowAddMenu(false);
      }
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) {
        setShowExportMenu(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleAddNode = useCallback(
    (type: StoryboardNode['node_type']) => {
      const newNode: StoryboardNode = {
        id: `node-${Date.now()}`,
        project_id: currentProjectId ?? '',
        node_type: type,
        position_x: 100 + nodes.length * 20,
        position_y: 100 + nodes.length * 20,
        data_json: {},
        sort_order: nodes.length,
        locked: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      addNode(newNode);
      setShowAddMenu(false);
    },
    [nodes.length, currentProjectId, addNode]
  );

  const canUndo = history.past.length > 0;
  const canRedo = history.future.length > 0;

  return (
    <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-1 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl px-2 py-1.5">
      {/* Add Node */}
      <div className="relative" ref={addMenuRef}>
        <button
          onClick={() => setShowAddMenu((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition-colors"
          title="Add Node"
        >
          <Plus size={15} />
          <span>Add</span>
          <ChevronDown size={12} />
        </button>
        {showAddMenu && (
          <div className="absolute top-full mt-1 left-0 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 min-w-[180px] z-30">
            {NODE_TYPE_OPTIONS.map((opt) => (
              <button
                key={opt.type}
                onClick={() => handleAddNode(opt.type)}
                className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-200 hover:bg-gray-700 transition-colors"
              >
                <span className="text-gray-400">{opt.icon}</span>
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <Divider />

      {/* Zoom In */}
      <ToolbarButton onClick={() => zoomIn()} title="Zoom In">
        <ZoomIn size={16} />
      </ToolbarButton>

      {/* Zoom Out */}
      <ToolbarButton onClick={() => zoomOut()} title="Zoom Out">
        <ZoomOut size={16} />
      </ToolbarButton>

      {/* Fit View */}
      <ToolbarButton onClick={() => fitView({ padding: 0.1 })} title="Fit View">
        <Maximize2 size={16} />
      </ToolbarButton>

      <Divider />

      {/* Lock */}
      <ToolbarButton
        onClick={onLockToggle}
        title={locked ? 'Unlock Canvas' : 'Lock Canvas'}
        active={locked}
      >
        {locked ? <Lock size={16} /> : <Unlock size={16} />}
      </ToolbarButton>

      <Divider />

      {/* Undo */}
      <ToolbarButton onClick={undo} title="Undo (Cmd+Z)" disabled={!canUndo}>
        <Undo2 size={16} />
      </ToolbarButton>

      {/* Redo */}
      <ToolbarButton onClick={redo} title="Redo (Cmd+Shift+Z)" disabled={!canRedo}>
        <Redo2 size={16} />
      </ToolbarButton>

      <Divider />

      {/* Export */}
      <div className="relative" ref={exportMenuRef}>
        <ToolbarButton
          onClick={() => setShowExportMenu((v) => !v)}
          title="Export"
        >
          <Download size={16} />
          <ChevronDown size={11} className="ml-0.5" />
        </ToolbarButton>
        {showExportMenu && (
          <div className="absolute top-full mt-1 right-0 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 min-w-[160px] z-30">
            {EXPORT_OPTIONS.map((opt) => (
              <button
                key={opt.format}
                onClick={() => setShowExportMenu(false)}
                className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-200 hover:bg-gray-700 transition-colors"
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Characters */}
      <ToolbarButton onClick={onCharactersToggle} title="Toggle Characters Panel">
        <Users size={16} />
      </ToolbarButton>
    </div>
  );
});

// ─── Sub-components ───────────────────────────────────────────────────────────

interface ToolbarButtonProps {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
  disabled?: boolean;
  active?: boolean;
}

function ToolbarButton({ onClick, title, children, disabled, active }: ToolbarButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={[
        'flex items-center justify-center gap-0.5 p-2 rounded-lg text-sm transition-colors',
        active
          ? 'bg-blue-600 text-white'
          : 'text-gray-300 hover:bg-gray-700 hover:text-white',
        disabled ? 'opacity-40 cursor-not-allowed' : '',
      ].join(' ')}
    >
      {children}
    </button>
  );
}

function Divider() {
  return <div className="w-px h-6 bg-gray-700 mx-0.5" />;
}

export default CanvasToolbar;
