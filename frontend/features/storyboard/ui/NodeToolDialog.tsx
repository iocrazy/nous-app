import { useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

import { useCanvasStore } from '../../../stores/canvasStore';
import { NODE_TOOL_TYPES, isUploadNode, isImageEditNode, isExportImageNode } from '../domain/canvasNodes';
import { resolveImageDisplayUrl } from '../application/imageData';
import { CropToolEditor } from './CropToolEditor';
import { AnnotateToolEditor } from './AnnotateToolEditor';
import { SplitStoryboardToolEditor } from './SplitStoryboardToolEditor';

export function NodeToolDialog() {
  const activeToolDialog = useCanvasStore((state) => state.activeToolDialog);
  const nodes = useCanvasStore((state) => state.nodes);
  const closeToolDialog = useCanvasStore((state) => state.closeToolDialog);
  const addDerivedExportNode = useCanvasStore((state) => state.addDerivedExportNode);
  const addStoryboardSplitNode = useCanvasStore((state) => state.addStoryboardSplitNode);
  const addEdge = useCanvasStore((state) => state.addEdge);

  const handleClose = useCallback(() => {
    closeToolDialog();
  }, [closeToolDialog]);

  if (!activeToolDialog) return null;

  const { nodeId, toolType } = activeToolDialog;
  const node = nodes.find((n) => n.id === nodeId);
  if (!node) return null;

  // Resolve source image URL
  let sourceImageUrl: string | null = null;
  if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
    sourceImageUrl = node.data.imageUrl;
  }

  if (!sourceImageUrl) {
    return null;
  }

  const displayUrl = resolveImageDisplayUrl(sourceImageUrl);

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
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-xl border border-[rgba(255,255,255,0.14)] bg-surface-dark shadow-2xl">
        <button
          type="button"
          onClick={handleClose}
          className="absolute right-2 top-2 z-10 flex h-6 w-6 items-center justify-center rounded-lg text-text-muted hover:bg-[rgba(255,255,255,0.1)] hover:text-text-dark"
        >
          <X className="h-4 w-4" />
        </button>

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
    </div>,
    document.body,
  );
}
