import { LayoutGrid, List, Minus, Plus, Square } from 'lucide-react';
import { useState } from 'react';
import { useOnViewportChange, useReactFlow } from '@xyflow/react';
import { useScriptCanvasStore } from '../../../stores/scriptCanvasStore';

type ViewMode = 'canvas' | 'grid' | 'list';

interface ViewButtonProps {
  onClick: () => void;
  isActive: boolean;
  title: string;
  children: React.ReactNode;
}

function ViewButton({ onClick, isActive, title, children }: ViewButtonProps) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`p-1.5 rounded transition-colors ${
        isActive ? 'bg-zinc-600 text-white' : 'text-zinc-400 hover:bg-zinc-700'
      }`}
    >
      {children}
    </button>
  );
}

interface ZoomButtonProps {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}

function ZoomButton({ onClick, title, children }: ZoomButtonProps) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="p-1.5 rounded text-zinc-400 hover:bg-zinc-700 transition-colors"
    >
      {children}
    </button>
  );
}

/** Zoom controls — must be rendered inside a ReactFlowProvider. */
function CanvasZoomControls() {
  const { zoomIn, zoomOut, getViewport } = useReactFlow();
  const [zoom, setZoom] = useState(() => Math.round(getViewport().zoom * 100));

  useOnViewportChange({
    onChange: (viewport) => {
      setZoom(Math.round(viewport.zoom * 100));
    },
  });

  return (
    <>
      <div className="w-px h-4 bg-zinc-600 mx-1" />
      <ZoomButton onClick={() => zoomOut()} title="Zoom out">
        <Minus className="w-4 h-4" />
      </ZoomButton>
      <span className="text-xs text-zinc-400 min-w-[36px] text-center select-none">
        {zoom}%
      </span>
      <ZoomButton onClick={() => zoomIn()} title="Zoom in">
        <Plus className="w-4 h-4" />
      </ZoomButton>
    </>
  );
}

/** View-mode switcher + optional zoom controls.
 *
 * When `showZoom` is true the component must be rendered inside a ReactFlowProvider
 * (i.e. inside ScriptCanvas). When false (grid/list views) it renders without any
 * ReactFlow dependency. */
interface ViewControlsProps {
  /** Whether to render zoom controls (requires ReactFlow context). Default false. */
  showZoom?: boolean;
}

export function ViewControls({ showZoom = false }: ViewControlsProps) {
  const { viewMode, setViewMode } = useScriptCanvasStore();

  const handleSetViewMode = (mode: ViewMode) => {
    setViewMode(mode);
  };

  return (
    <div className="absolute bottom-3 right-3 flex items-center gap-1 bg-zinc-800/90 backdrop-blur-sm rounded-lg p-1 border border-zinc-700 z-10">
      {/* View mode buttons */}
      <ViewButton
        onClick={() => handleSetViewMode('grid')}
        isActive={viewMode === 'grid'}
        title="Grid view"
      >
        <LayoutGrid className="w-4 h-4" />
      </ViewButton>

      <ViewButton
        onClick={() => handleSetViewMode('list')}
        isActive={viewMode === 'list'}
        title="List view"
      >
        <List className="w-4 h-4" />
      </ViewButton>

      <ViewButton
        onClick={() => handleSetViewMode('canvas')}
        isActive={viewMode === 'canvas'}
        title="Canvas view"
      >
        <Square className="w-4 h-4" />
      </ViewButton>

      {/* Zoom controls — only rendered inside ReactFlow context */}
      {showZoom && <CanvasZoomControls />}
    </div>
  );
}
