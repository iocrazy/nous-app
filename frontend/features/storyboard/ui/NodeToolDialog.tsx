import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

import { useCanvasStore } from '../../../stores/canvasStore';
import { NODE_TOOL_TYPES, isUploadNode, isImageEditNode, isExportImageNode } from '../domain/canvasNodes';
import { resolveImageDisplayUrl } from '../application/imageData';
import { CropToolEditor } from './CropToolEditor';
import { AnnotateToolEditor } from './AnnotateToolEditor';
import { SplitStoryboardToolEditor } from './SplitStoryboardToolEditor';

const TRANSITION_DURATION_MS = 180;

function resolveToolTitle(toolType: string): string {
  if (toolType === NODE_TOOL_TYPES.crop) return 'Crop Image';
  if (toolType === NODE_TOOL_TYPES.annotate) return 'Annotate Image';
  if (toolType === NODE_TOOL_TYPES.splitStoryboard) return 'Split into Storyboard';
  return 'Tool';
}

function resolveDialogWidth(toolType: string): string {
  if (toolType === NODE_TOOL_TYPES.annotate) return 'max-w-2xl';
  if (toolType === NODE_TOOL_TYPES.splitStoryboard) return 'max-w-lg';
  return 'max-w-md';
}

export function NodeToolDialog() {
  const activeToolDialog = useCanvasStore((state) => state.activeToolDialog);
  const nodes = useCanvasStore((state) => state.nodes);
  const closeToolDialog = useCanvasStore((state) => state.closeToolDialog);
  const addDerivedExportNode = useCanvasStore((state) => state.addDerivedExportNode);
  const addStoryboardSplitNode = useCanvasStore((state) => state.addStoryboardSplitNode);
  const addEdge = useCanvasStore((state) => state.addEdge);

  // Animation state: show the dialog content during close transition
  const [displayedDialog, setDisplayedDialog] = useState(activeToolDialog);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    if (activeToolDialog) {
      setDisplayedDialog(activeToolDialog);
      // Trigger enter animation on next frame
      requestAnimationFrame(() => {
        setIsVisible(true);
      });
    } else {
      setIsVisible(false);
      const timer = setTimeout(() => {
        setDisplayedDialog(null);
      }, TRANSITION_DURATION_MS);
      return () => clearTimeout(timer);
    }
  }, [activeToolDialog]);

  const handleClose = useCallback(() => {
    closeToolDialog();
  }, [closeToolDialog]);

  // Esc key to close
  useEffect(() => {
    if (!displayedDialog) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        handleClose();
      }
    };

    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [displayedDialog, handleClose]);

  const node = useMemo(() => {
    if (!displayedDialog) return null;
    return nodes.find((n) => n.id === displayedDialog.nodeId) ?? null;
  }, [displayedDialog, nodes]);

  const sourceImageUrl = useMemo(() => {
    if (!node) return null;
    if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
      return node.data.imageUrl;
    }
    return null;
  }, [node]);

  if (!displayedDialog || !node || !sourceImageUrl) return null;

  const { nodeId, toolType } = displayedDialog;
  const displayUrl = resolveImageDisplayUrl(sourceImageUrl);
  const dialogTitle = resolveToolTitle(toolType);
  const widthClass = resolveDialogWidth(toolType);

  const handleCropConfirm = (resultUrl: string) => {
    const aspectRatio = (node.data as { aspectRatio?: string }).aspectRatio || '1:1';
    const newNodeId = addDerivedExportNode(nodeId, resultUrl, aspectRatio, undefined, {
      resultKind: 'storyboardFrameEdit',
      defaultTitle: 'Cropped',
    });
    if (newNodeId) {
      addEdge(nodeId, newNodeId);
    }
    closeToolDialog();
  };

  const handleAnnotateConfirm = (resultUrl: string) => {
    const aspectRatio = (node.data as { aspectRatio?: string }).aspectRatio || '1:1';
    const newNodeId = addDerivedExportNode(nodeId, resultUrl, aspectRatio, undefined, {
      resultKind: 'storyboardFrameEdit',
      defaultTitle: 'Annotated',
    });
    if (newNodeId) {
      addEdge(nodeId, newNodeId);
    }
    closeToolDialog();
  };

  const handleSplitConfirm = (result: {
    rows: number;
    cols: number;
    frames: Array<{
      id: string;
      imageUrl: string | null;
      previewImageUrl?: string | null;
      aspectRatio?: string;
      note: string;
      order: number;
    }>;
    frameAspectRatio?: string;
  }) => {
    const newNodeId = addStoryboardSplitNode(
      nodeId,
      result.rows,
      result.cols,
      result.frames,
      result.frameAspectRatio,
    );
    if (newNodeId) {
      addEdge(nodeId, newNodeId);
    }
    closeToolDialog();
  };

  return createPortal(
    <div
      className={`fixed inset-0 z-[200] flex items-center justify-center transition-colors duration-[180ms] ${
        isVisible ? 'bg-black/60' : 'bg-black/0'
      }`}
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div
        className={`relative w-full ${widthClass} overflow-hidden rounded-xl border border-[rgba(255,255,255,0.14)] bg-surface-dark shadow-2xl transition-all duration-[180ms] ${
          isVisible
            ? 'scale-100 opacity-100'
            : 'scale-95 opacity-0'
        }`}
      >
        {/* Header bar */}
        <div className="flex items-center justify-between border-b border-[rgba(255,255,255,0.08)] px-4 py-2.5">
          <span className="text-sm font-medium text-text-dark">{dialogTitle}</span>
          <button
            type="button"
            onClick={handleClose}
            className="flex h-6 w-6 items-center justify-center rounded-lg text-text-muted hover:bg-[rgba(255,255,255,0.1)] hover:text-text-dark transition-colors"
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[80vh] overflow-y-auto">
          {toolType === NODE_TOOL_TYPES.crop && (
            <CropToolEditor
              imageUrl={displayUrl}
              onConfirm={handleCropConfirm}
              onCancel={handleClose}
            />
          )}

          {toolType === NODE_TOOL_TYPES.annotate && (
            <AnnotateToolEditor
              imageUrl={displayUrl}
              onConfirm={handleAnnotateConfirm}
              onCancel={handleClose}
            />
          )}

          {toolType === NODE_TOOL_TYPES.splitStoryboard && (
            <SplitStoryboardToolEditor
              imageUrl={displayUrl}
              onConfirm={handleSplitConfirm}
              onCancel={handleClose}
            />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
