import {
  memo,
  useEffect,
  useMemo,
  useState,
  useCallback,
  useRef,
} from 'react';
import { createPortal } from 'react-dom';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  useViewport,
  type NodeProps,
} from '@xyflow/react';
import { Download, ImageDown, ImagePlus, Loader2, SlidersHorizontal, SquareArrowOutUpRight } from 'lucide-react';

import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '../ui/NodeHeader';
import { NodeResizeHandle } from '../ui/NodeResizeHandle';
import { CanvasNodeImage } from '../ui/CanvasNodeImage';
import type {
  CanvasNode,
  StoryboardExportOptions,
  StoryboardFrameItem,
  StoryboardSplitNodeData,
} from '../domain/canvasNodes';
import {
  CANVAS_NODE_TYPES,
  isExportImageNode,
  isImageEditNode,
  isUploadNode,
} from '../domain/canvasNodes';
import { resolveNodeDisplayName } from '../domain/nodeDisplay';
import { resolveImageDisplayUrl, shouldUseOriginalImageByZoom } from '../application/imageData';
import { UiButton, UiCheckbox, UiChipButton, UiInput, UiPanel, UiSelect } from '../../../components/ui';
import { NODE_CONTROL_CHIP_CLASS, NODE_CONTROL_ICON_CLASS, NODE_CONTROL_PRIMARY_BUTTON_CLASS } from '../ui/nodeControlStyles';
import { useCanvasStore } from '../../../stores/canvasStore';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { exportProject, type ExportFormat } from '../../../services/storyboardService';
import { mergeStoryboardFrames, downloadDataUrl, type MergeFrameInput } from '../application/storyboardMerge';

type StoryboardNodeProps = NodeProps & {
  id: string;
  data: StoryboardSplitNodeData;
  selected?: boolean;
};

const STORYBOARD_NODE_WIDTH_PX = 318;
const STORYBOARD_NODE_MIN_HEIGHT_PX = 320;
const STORYBOARD_GRID_GAP_PX = 1;

function SplitResultIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M10 0c1.66 0 3 1.34 3 3v3l2.4-1.5a3.003 3.003 0 0 1 3 5.2a3.003 3.003 0 0 1-4.452-2.051l-.952.55v6.8h-2v-5.65l-4.01 2.32l-.988-1.73l5-2.94v-1.17a2.996 2.996 0 0 1-4-2.829c0-1.66 1.34-3 3-3zM9 3a1 1 0 0 0 2 0a1 1 0 0 0-2 0m7 4a1 1 0 0 0 2 0a1 1 0 0 0-2 0M2.97 19h2v-2h-2V9h3V7h-3c-1.1 0-2 .895-2 2v8c0 1.1.895 2 2 2m6 0h-2v-2h2zm4-2c0 1.1-.895 2-2 2v-2z" />
    </svg>
  );
}

function toCssAspectRatio(aspectRatio: string): string {
  const [rawWidth = '1', rawHeight = '1'] = aspectRatio.split(':');
  const width = Number(rawWidth);
  const height = Number(rawHeight);

  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return '1 / 1';
  }

  return `${width} / ${height}`;
}

function createDefaultExportOptions(): StoryboardExportOptions {
  return {
    showFrameIndex: false,
    showFrameNote: false,
    notePlacement: 'overlay',
    imageFit: 'cover',
    frameIndexPrefix: 'S',
    cellGap: 8,
    outerPadding: 0,
    fontSize: 4,
    backgroundColor: '#0f1115',
    textColor: '#f8fafc',
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function resolveExportOptions(options: StoryboardSplitNodeData['exportOptions']): StoryboardExportOptions {
  const merged = {
    ...createDefaultExportOptions(),
    ...(options ?? {}),
  };

  const rawFontSize = Number.isFinite(merged.fontSize) ? merged.fontSize : 4;
  const normalizedFontPercent = rawFontSize > 20
    ? Math.round(rawFontSize / 6)
    : rawFontSize;

  return {
    ...merged,
    fontSize: clamp(Math.round(normalizedFontPercent), 1, 20),
  };
}

interface FrameContextMenuState {
  frameId: string;
  frameIndex: number;
  x: number;
  y: number;
}

interface FrameCardProps {
  nodeId: string;
  frame: StoryboardFrameItem;
  index: number;
  totalFrames: number;
  frameAspectRatioCss: string;
  imageFit: StoryboardExportOptions['imageFit'];
  viewerImageList: string[];
  draggedFrameId: string | null;
  dropTargetFrameId: string | null;
  onSortStart: (frameId: string) => void;
  onSortHover: (frameId: string) => void;
  onTogglePicker: (frameId: string, x: number, y: number) => void;
  onEditFrame: (frame: StoryboardFrameItem) => void;
  onContextMenu: (state: FrameContextMenuState) => void;
}

interface IncomingImageItem {
  imageUrl: string;
  previewImageUrl: string | null;
  displayUrl: string;
  label: string;
}

interface PanelAnchor {
  left: number;
  top: number;
}

const FrameCard = memo(
  ({
    nodeId,
    frame,
    index,
    totalFrames,
    frameAspectRatioCss,
    imageFit,
    viewerImageList,
    draggedFrameId,
    dropTargetFrameId,
    onSortStart,
    onSortHover,
    onTogglePicker,
    onEditFrame,
    onContextMenu,
  }: FrameCardProps) => {
    const updateStoryboardFrame = useCanvasStore((state) => state.updateStoryboardFrame);
    const { zoom } = useViewport();

    const imageSource = useMemo(() => {
      const preferOriginal = shouldUseOriginalImageByZoom(zoom);
      const picked = preferOriginal
        ? frame.imageUrl || frame.previewImageUrl
        : frame.previewImageUrl || frame.imageUrl;
      return picked ? resolveImageDisplayUrl(picked) : null;
    }, [frame.imageUrl, frame.previewImageUrl, zoom]);
    const viewerSource = useMemo(() => {
      const picked = frame.imageUrl || frame.previewImageUrl;
      return picked ? resolveImageDisplayUrl(picked) : null;
    }, [frame.imageUrl, frame.previewImageUrl]);

    const dragging = draggedFrameId === frame.id;
    const asDropTarget = dropTargetFrameId === frame.id && !dragging;

    return (
      <div
        onPointerEnter={(event) => {
          event.stopPropagation();
          onSortHover(frame.id);
        }}
        onPointerMove={(event) => {
          event.stopPropagation();
          onSortHover(frame.id);
        }}
        onMouseDown={(event) => event.stopPropagation()}
        onContextMenu={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onContextMenu({ frameId: frame.id, frameIndex: index, x: event.clientX, y: event.clientY });
        }}
        className={`nodrag relative bg-bg-dark/85 transition-colors ${dragging
          ? 'z-10 opacity-55 ring-1 ring-accent/65'
          : asDropTarget
            ? 'z-10 ring-1 ring-emerald-400/70'
            : ''
          }`}
      >
        <div
          className={`group/frame relative overflow-hidden bg-surface-dark ${dragging ? 'cursor-grabbing' : 'cursor-grab'}`}
          style={{ aspectRatio: frameAspectRatioCss }}
          onPointerDown={(event) => {
            if (event.button !== 0) {
              return;
            }
            event.preventDefault();
            event.stopPropagation();
            onSortStart(frame.id);
          }}
        >
          {frame.imageUrl ? (
            <CanvasNodeImage
              src={imageSource ?? ''}
              alt={`Frame ${index + 1}`}
              viewerSourceUrl={viewerSource}
              viewerImageList={viewerImageList}
              className={`h-full w-full ${imageFit === 'contain' ? 'object-contain' : 'object-cover'}`}
              draggable={false}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-[11px] text-text-muted">
              Empty
            </div>
          )}

          <button
            type="button"
            className="absolute right-1 top-1 rounded bg-black/60 p-1 text-white opacity-0 transition-all duration-150 hover:bg-black/75 group-hover/frame:opacity-100"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              onEditFrame(frame);
            }}
            title="Edit this frame"
          >
            <SquareArrowOutUpRight className="h-3 w-3" />
          </button>

          <button
            type="button"
            className="absolute bottom-1 right-1 rounded bg-black/60 p-1 text-white opacity-0 transition-all duration-150 hover:bg-black/75 group-hover/frame:opacity-100"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              onTogglePicker(frame.id, event.clientX, event.clientY);
            }}
            title="Replace from input"
          >
            <ImagePlus className="h-3 w-3" />
          </button>
        </div>

        <textarea
          value={frame.note}
          onChange={(event) => {
            const nextValue = event.target.value;
            updateStoryboardFrame(nodeId, frame.id, {
              note: nextValue,
            });
          }}
          onMouseDown={(event) => event.stopPropagation()}
          onWheelCapture={(event) => event.stopPropagation()}
          placeholder={`Frame ${String(index + 1).padStart(2, '0')} description`}
          className="ui-scrollbar nodrag nowheel h-10 w-full resize-none overflow-y-auto border-0 border-t border-[rgba(255,255,255,0.12)] bg-bg-dark/90 px-2 py-1 text-[10px] text-text-dark outline-none focus:border-accent"
        />
      </div>
    );
  }
);

FrameCard.displayName = 'FrameCard';

export const StoryboardNode = memo(({ id, data, selected, width, height }: StoryboardNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const rootRef = useRef<HTMLDivElement>(null);
  const pickerMenuRef = useRef<HTMLDivElement>(null);
  const exportSettingsTriggerRef = useRef<HTMLDivElement>(null);
  const exportSettingsPanelRef = useRef<HTMLDivElement>(null);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const reorderStoryboardFrame = useCanvasStore((state) => state.reorderStoryboardFrame);
  const updateStoryboardFrame = useCanvasStore((state) => state.updateStoryboardFrame);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const [draggedFrameId, setDraggedFrameId] = useState<string | null>(null);
  const [dropTargetFrameId, setDropTargetFrameId] = useState<string | null>(null);
  const [pickerState, setPickerState] = useState<{ frameId: string; x: number; y: number } | null>(null);
  const currentProjectId = useStoryboardStore((state) => state.currentProjectId);
  const [exportError, setExportError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isExportPanelOpen, setIsExportPanelOpen] = useState(false);
  const [isExportPanelVisible, setIsExportPanelVisible] = useState(false);
  const [exportPanelAnchor, setExportPanelAnchor] = useState<PanelAnchor | null>(null);

  const orderedFrames = useMemo(
    () => [...data.frames].sort((a, b) => a.order - b.order),
    [data.frames]
  );

  const frameAspectRatio = useMemo(() => {
    return (
      data.frameAspectRatio ??
      orderedFrames.find((frame) => typeof frame.aspectRatio === 'string')?.aspectRatio ??
      '1:1'
    );
  }, [data.frameAspectRatio, orderedFrames]);

  const frameAspectRatioCss = useMemo(
    () => toCssAspectRatio(frameAspectRatio),
    [frameAspectRatio]
  );

  const gridCols = Math.max(1, data.gridCols);
  const gridRows = Math.max(1, data.gridRows);
  const totalFrames = orderedFrames.length;
  const resolvedNodeWidth = Math.max(STORYBOARD_NODE_WIDTH_PX, Math.round(width ?? STORYBOARD_NODE_WIDTH_PX));
  const resolvedNodeHeight = Math.max(
    STORYBOARD_NODE_MIN_HEIGHT_PX,
    Math.round(height ?? STORYBOARD_NODE_MIN_HEIGHT_PX)
  );

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedNodeHeight, resolvedNodeWidth, updateNodeInternals]);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.storyboardSplit, data),
    [data]
  );

  const exportOptions = useMemo(
    () => resolveExportOptions(data.exportOptions),
    [data.exportOptions]
  );

  const incomingImageRefs = useMemo(() => {
    const nodeById = new Map(nodes.map((node) => [node.id, node] as const));
    const sourceNodeIds = edges
      .filter((edge) => edge.target === id)
      .map((edge) => edge.source);

    const dedupedByImageUrl = new Map<string, { imageUrl: string; previewImageUrl: string | null }>();
    for (const sourceNodeId of sourceNodeIds) {
      const sourceNode = nodeById.get(sourceNodeId) as CanvasNode | undefined;
      if (!sourceNode) continue;
      if (!isUploadNode(sourceNode) && !isImageEditNode(sourceNode) && !isExportImageNode(sourceNode)) continue;
      const imageUrl = sourceNode.data.imageUrl;
      if (!imageUrl) continue;
      if (!dedupedByImageUrl.has(imageUrl)) {
        dedupedByImageUrl.set(imageUrl, {
          imageUrl,
          previewImageUrl: sourceNode.data.previewImageUrl ?? null,
        });
      }
    }

    return Array.from(dedupedByImageUrl.values());
  }, [edges, id, nodes]);

  const incomingImageItems = useMemo<IncomingImageItem[]>(
    () =>
      incomingImageRefs.map((item, index) => ({
        imageUrl: item.imageUrl,
        previewImageUrl: item.previewImageUrl,
        displayUrl: resolveImageDisplayUrl(item.previewImageUrl || item.imageUrl),
        label: `Image ${index + 1}`,
      })),
    [incomingImageRefs]
  );

  const frameViewerImageList = useMemo(
    () =>
      orderedFrames
        .map((frame) => {
          const source = frame.imageUrl || frame.previewImageUrl;
          return source ? resolveImageDisplayUrl(source) : null;
        })
        .filter((item): item is string => Boolean(item)),
    [orderedFrames]
  );

  const incomingImageViewerList = useMemo(
    () => incomingImageItems.map((item) => resolveImageDisplayUrl(item.imageUrl)),
    [incomingImageItems]
  );

  useEffect(() => {
    const handleOutsidePointerDown = (event: PointerEvent) => {
      if (!rootRef.current) return;
      const target = event.target as Node;
      const insideRoot = rootRef.current.contains(target);
      const insidePickerMenu = pickerMenuRef.current?.contains(target) ?? false;
      const insideExportPanel = exportSettingsPanelRef.current?.contains(target) ?? false;
      const insideExportTrigger = exportSettingsTriggerRef.current?.contains(target) ?? false;

      if (!insideRoot && !insidePickerMenu) setPickerState(null);
      if (!insideExportPanel && !insideExportTrigger) setIsExportPanelOpen(false);
    };

    document.addEventListener('pointerdown', handleOutsidePointerDown, true);
    return () => document.removeEventListener('pointerdown', handleOutsidePointerDown, true);
  }, []);

  useEffect(() => {
    if (!isExportPanelOpen) {
      setIsExportPanelVisible(false);
      return;
    }
    let raf2: number | null = null;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setIsExportPanelVisible(true));
    });
    return () => {
      cancelAnimationFrame(raf1);
      if (raf2 !== null) cancelAnimationFrame(raf2);
    };
  }, [isExportPanelOpen]);

  const getPanelAnchor = useCallback((triggerElement: HTMLDivElement | null): PanelAnchor | null => {
    if (!triggerElement) return null;
    const rect = triggerElement.getBoundingClientRect();
    return { left: rect.left + rect.width / 2, top: rect.top - 8 };
  }, []);

  const patchExportOptions = useCallback(
    (patch: Partial<StoryboardExportOptions>) => {
      updateNodeData(id, { exportOptions: { ...exportOptions, ...patch } });
    },
    [exportOptions, id, updateNodeData]
  );

  const handleSortStart = useCallback((frameId: string) => {
    setDraggedFrameId(frameId);
    setDropTargetFrameId(frameId);
    setPickerState(null);
  }, []);

  const handleSortHover = useCallback(
    (frameId: string) => { if (draggedFrameId) setDropTargetFrameId(frameId); },
    [draggedFrameId]
  );

  const finalizeSort = useCallback(() => {
    if (!draggedFrameId) return;
    if (dropTargetFrameId && dropTargetFrameId !== draggedFrameId) {
      reorderStoryboardFrame(id, draggedFrameId, dropTargetFrameId);
    }
    setDraggedFrameId(null);
    setDropTargetFrameId(null);
  }, [draggedFrameId, dropTargetFrameId, id, reorderStoryboardFrame]);

  useEffect(() => {
    if (!draggedFrameId) return;
    const handlePointerUp = () => finalizeSort();
    const previousUserSelect = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'grabbing';
    window.addEventListener('pointerup', handlePointerUp);
    window.addEventListener('pointercancel', handlePointerUp);
    return () => {
      document.body.style.userSelect = previousUserSelect;
      document.body.style.cursor = previousCursor;
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('pointercancel', handlePointerUp);
    };
  }, [draggedFrameId, finalizeSort]);

  const handleEditFrame = useCallback((_frame: StoryboardFrameItem) => {
    // Open frame in image viewer for now; full editing can be added later
    const imageUrl = _frame.imageUrl || _frame.previewImageUrl;
    if (imageUrl) {
      const { openImageViewer } = useCanvasStore.getState();
      openImageViewer(resolveImageDisplayUrl(imageUrl), frameViewerImageList);
    }
  }, [frameViewerImageList]);

  const handleExport = useCallback(async (format: ExportFormat = 'png') => {
    if (!currentProjectId) {
      setExportError('No project context');
      return;
    }
    setExportError(null);
    setIsExporting(true);
    try {
      const result = await exportProject(currentProjectId, format, {
        includeFrameNumbers: exportOptions.showFrameIndex,
        includeNotes: exportOptions.showFrameNote,
        columns: gridCols,
      });
      // Task dispatched — the task manager handles progress tracking
      setExportError(null);
      console.info('[StoryboardNode] Export queued — task_id:', result.task_id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setExportError(`Export failed: ${message}`);
    } finally {
      setIsExporting(false);
    }
  }, [currentProjectId, exportOptions.showFrameIndex, exportOptions.showFrameNote, gridCols]);

  const [isMerging, setIsMerging] = useState(false);

  const handleLocalMerge = useCallback(async () => {
    setExportError(null);
    setIsMerging(true);
    try {
      const mergeFrames: MergeFrameInput[] = orderedFrames
        .filter((frame) => frame.imageUrl)
        .map((frame) => ({
          imageUrl: resolveImageDisplayUrl(frame.imageUrl!),
          note: frame.note || undefined,
        }));
      if (mergeFrames.length === 0) {
        setExportError('No frames with images to merge');
        return;
      }
      const dataUrl = await mergeStoryboardFrames(mergeFrames, {
        rows: gridRows,
        cols: gridCols,
        gap: exportOptions.cellGap,
        padding: exportOptions.outerPadding,
        backgroundColor: exportOptions.backgroundColor,
        showFrameNumbers: exportOptions.showFrameIndex,
        frameNumberColor: exportOptions.textColor,
        frameNumberSize: Math.round(exportOptions.fontSize * 6),
        frameNumberPrefix: exportOptions.frameIndexPrefix,
        showNotes: exportOptions.showFrameNote,
        noteColor: exportOptions.textColor,
        noteSize: Math.round(exportOptions.fontSize * 3.5),
        notePlacement: exportOptions.notePlacement === 'bottom' ? 'below' : 'overlay',
      });
      const timestamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-');
      downloadDataUrl(dataUrl, `storyboard-${timestamp}.png`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setExportError(`Local merge failed: ${message}`);
    } finally {
      setIsMerging(false);
    }
  }, [orderedFrames, gridRows, gridCols, exportOptions]);

  const handleTogglePicker = useCallback((frameId: string, x: number, y: number) => {
    setPickerState((previous) => {
      if (previous?.frameId === frameId) return null;
      return { frameId, x, y };
    });
  }, []);

  const handleReplaceFromInput = useCallback(
    (frameId: string, imageUrl: string) => {
      setExportError(null);
      const matched = incomingImageItems.find((item) => item.imageUrl === imageUrl);
      updateStoryboardFrame(id, frameId, {
        imageUrl: matched?.imageUrl ?? imageUrl,
        previewImageUrl: matched?.previewImageUrl ?? matched?.imageUrl ?? imageUrl,
      });
      setPickerState(null);
    },
    [id, incomingImageItems, updateStoryboardFrame]
  );

  // Frame context menu state
  const [frameContextMenu, setFrameContextMenu] = useState<FrameContextMenuState | null>(null);
  const frameContextMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!frameContextMenu) return;
    const handleClose = (e: PointerEvent) => {
      if (frameContextMenuRef.current?.contains(e.target as Node)) return;
      setFrameContextMenu(null);
    };
    document.addEventListener('pointerdown', handleClose, true);
    return () => document.removeEventListener('pointerdown', handleClose, true);
  }, [frameContextMenu]);

  const handleFrameContextMenu = useCallback((state: FrameContextMenuState) => {
    setFrameContextMenu(state);
    setPickerState(null);
  }, []);

  const handleDuplicateFrame = useCallback((frameId: string) => {
    const frame = data.frames.find((f) => f.id === frameId);
    if (!frame) return;
    const maxOrder = Math.max(...data.frames.map((f) => f.order), 0);
    const newFrame: StoryboardFrameItem = {
      ...frame,
      id: `frame-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      order: maxOrder + 1,
    };
    updateNodeData(id, { frames: [...data.frames, newFrame] });
    setFrameContextMenu(null);
  }, [data.frames, id, updateNodeData]);

  const handleDeleteFrame = useCallback((frameId: string) => {
    if (data.frames.length <= 1) return;
    updateNodeData(id, { frames: data.frames.filter((f) => f.id !== frameId) });
    setFrameContextMenu(null);
  }, [data.frames, id, updateNodeData]);

  const handleMoveFrame = useCallback((frameId: string, direction: 'up' | 'down') => {
    const sorted = [...data.frames].sort((a, b) => a.order - b.order);
    const idx = sorted.findIndex((f) => f.id === frameId);
    if (idx < 0) return;
    const targetIdx = direction === 'up' ? idx - 1 : idx + 1;
    if (targetIdx < 0 || targetIdx >= sorted.length) return;
    // Swap orders
    const frameA = sorted[idx];
    const frameB = sorted[targetIdx];
    const updatedFrames = data.frames.map((f) => {
      if (f.id === frameA.id) return { ...f, order: frameB.order };
      if (f.id === frameB.id) return { ...f, order: frameA.order };
      return f;
    });
    updateNodeData(id, { frames: updatedFrames });
    setFrameContextMenu(null);
  }, [data.frames, id, updateNodeData]);

  // Metadata summary
  const framesWithImages = orderedFrames.filter((f) => f.imageUrl).length;

  return (
    <div
      ref={rootRef}
      className={`
        group relative flex h-full flex-col overflow-visible rounded-[var(--node-radius)] border bg-surface-dark/90 p-2 transition-colors duration-150
        ${selected
          ? 'border-accent shadow-[0_0_0_1px_rgba(59,130,246,0.32)]'
          : 'border-[rgba(15,23,42,0.22)] hover:border-[rgba(15,23,42,0.34)] dark:border-[rgba(255,255,255,0.22)] dark:hover:border-[rgba(255,255,255,0.34)]'}
      `}
      style={{ width: `${resolvedNodeWidth}px`, height: `${resolvedNodeHeight}px` }}
      onClick={() => setSelectedNode(id)}
    >
      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<SplitResultIcon className="h-3.5 w-3.5" />}
        titleText={resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <div
        className="ui-scrollbar nowheel min-h-0 flex-1 overflow-auto"
        onWheelCapture={(event) => event.stopPropagation()}
      >
        <div
          className="grid overflow-hidden rounded-lg border border-[rgba(255,255,255,0.16)] bg-[rgba(255,255,255,0.14)]"
          style={{
            gap: `${STORYBOARD_GRID_GAP_PX}px`,
            gridTemplateColumns: `repeat(${gridCols}, minmax(0, 1fr))`,
          }}
        >
          {orderedFrames.map((frame, index) => (
            <FrameCard
              key={frame.id}
              nodeId={id}
              frame={frame}
              index={index}
              totalFrames={totalFrames}
              frameAspectRatioCss={frameAspectRatioCss}
              imageFit={exportOptions.imageFit}
              viewerImageList={frameViewerImageList}
              draggedFrameId={draggedFrameId}
              dropTargetFrameId={dropTargetFrameId}
              onSortStart={handleSortStart}
              onSortHover={handleSortHover}
              onTogglePicker={handleTogglePicker}
              onEditFrame={handleEditFrame}
              onContextMenu={handleFrameContextMenu}
            />
          ))}
        </div>
      </div>

      {pickerState && typeof document !== 'undefined'
        ? createPortal(
          <div
            ref={pickerMenuRef}
            className="nowheel fixed z-[140] w-[120px] overflow-hidden rounded-xl border border-[rgba(255,255,255,0.16)] bg-surface-dark shadow-xl"
            style={{ left: `${pickerState.x}px`, top: `${pickerState.y}px` }}
            onMouseDown={(event) => event.stopPropagation()}
            onWheelCapture={(event) => event.stopPropagation()}
          >
            {incomingImageItems.length > 0 ? (
              <div className="ui-scrollbar nowheel max-h-[180px] overflow-y-auto" onWheelCapture={(event) => event.stopPropagation()}>
                {incomingImageItems.map((item) => (
                  <button
                    key={`${pickerState.frameId}-${item.imageUrl}`}
                    type="button"
                    className="flex w-full items-center gap-2 border border-transparent bg-bg-dark/70 px-2 py-2 text-left text-sm text-text-dark transition-colors hover:border-[rgba(255,255,255,0.18)]"
                    onClick={(event) => {
                      event.stopPropagation();
                      handleReplaceFromInput(pickerState.frameId, item.imageUrl);
                    }}
                  >
                    <CanvasNodeImage
                      src={item.displayUrl}
                      alt={item.label}
                      viewerSourceUrl={resolveImageDisplayUrl(item.imageUrl)}
                      viewerImageList={incomingImageViewerList}
                      className="h-8 w-8 rounded object-cover"
                      draggable={false}
                    />
                    <span className="truncate">{item.label}</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="px-2 py-2 text-sm text-text-muted">No input images</div>
            )}
          </div>,
          document.body
        )
        : null}

      <div className="mt-2 flex shrink-0 items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <div ref={exportSettingsTriggerRef} className="nodrag relative flex">
            <UiChipButton
              active={isExportPanelOpen}
              className={NODE_CONTROL_CHIP_CLASS}
              onClick={(event) => {
                event.stopPropagation();
                if (isExportPanelOpen) { setIsExportPanelOpen(false); return; }
                setExportPanelAnchor(getPanelAnchor(exportSettingsTriggerRef.current));
                setIsExportPanelOpen(true);
              }}
            >
              <SlidersHorizontal className={`${NODE_CONTROL_ICON_CLASS} shrink-0`} />
              <span>Export Settings</span>
            </UiChipButton>
          </div>
          <div className="truncate text-[11px] text-text-muted/80">
            {gridRows} x {gridCols} | {totalFrames} frames
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <UiButton
            size="sm"
            variant="muted"
            disabled={isMerging}
            className={`nodrag ${NODE_CONTROL_PRIMARY_BUTTON_CLASS}`}
            onClick={(event) => {
              event.stopPropagation();
              void handleLocalMerge();
            }}
            title="Merge frames locally and download"
          >
            {isMerging ? (
              <Loader2 className={`${NODE_CONTROL_ICON_CLASS} animate-spin`} />
            ) : (
              <ImageDown className={NODE_CONTROL_ICON_CLASS} />
            )}
            {isMerging ? 'Merging...' : 'Save PNG'}
          </UiButton>
          <UiButton
            size="sm"
            variant="primary"
            disabled={isExporting}
            className={`nodrag ${NODE_CONTROL_PRIMARY_BUTTON_CLASS}`}
            onClick={(event) => {
              event.stopPropagation();
              void handleExport('png');
            }}
          >
            {isExporting ? (
              <Loader2 className={`${NODE_CONTROL_ICON_CLASS} animate-spin`} />
            ) : (
              <Download className={NODE_CONTROL_ICON_CLASS} />
            )}
            {isExporting ? 'Exporting...' : 'Export PNG'}
          </UiButton>
        </div>
      </div>

      {typeof document !== 'undefined' && isExportPanelOpen && createPortal(
        <div
          ref={exportSettingsPanelRef}
          className={`fixed z-[120] w-[340px] transition-opacity duration-200 ease-out ${isExportPanelVisible ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
          style={exportPanelAnchor
            ? { left: exportPanelAnchor.left, top: exportPanelAnchor.top, transform: 'translateX(-50%) translateY(-100%)' }
            : undefined}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <UiPanel className="p-2.5">
            <div className="space-y-2 text-xs text-text-muted">
              <label className="flex items-center gap-2">
                <UiCheckbox checked={exportOptions.showFrameIndex} onCheckedChange={(checked) => patchExportOptions({ showFrameIndex: checked })} />
                Show frame index
              </label>
              <label className="flex items-center gap-2">
                <UiCheckbox checked={exportOptions.showFrameNote} onCheckedChange={(checked) => patchExportOptions({ showFrameNote: checked })} />
                Show frame note
              </label>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <div className="mb-1">Image fit</div>
                  <UiSelect value={exportOptions.imageFit} onChange={(event) => patchExportOptions({ imageFit: event.target.value === 'contain' ? 'contain' : 'cover' })}>
                    <option value="cover">Cover</option>
                    <option value="contain">Contain</option>
                  </UiSelect>
                </div>
                <div>
                  <div className="mb-1">Index prefix</div>
                  <UiInput value={exportOptions.frameIndexPrefix} maxLength={4} className="h-8" onChange={(event) => patchExportOptions({ frameIndexPrefix: event.target.value })} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <div className="mb-1">Note placement</div>
                  <UiSelect value={exportOptions.notePlacement} onChange={(event) => patchExportOptions({ notePlacement: event.target.value === 'bottom' ? 'bottom' : 'overlay' })}>
                    <option value="overlay">Overlay</option>
                    <option value="bottom">Below</option>
                  </UiSelect>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <div className="mb-1">Gap</div>
                  <UiInput type="number" min={0} max={120} value={exportOptions.cellGap} className="h-8" onChange={(event) => patchExportOptions({ cellGap: Number(event.target.value) || 0 })} />
                </div>
                <div>
                  <div className="mb-1">Font size (%)</div>
                  <UiInput type="number" min={1} max={20} value={exportOptions.fontSize} className="h-8" onChange={(event) => patchExportOptions({ fontSize: Number(event.target.value) || 4 })} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="flex items-center gap-2">
                  <span>Background</span>
                  <input
                    type="color"
                    value={exportOptions.backgroundColor}
                    onChange={(event) => patchExportOptions({ backgroundColor: event.target.value })}
                    className="h-7 w-full rounded border border-[rgba(255,255,255,0.14)] bg-transparent"
                  />
                </label>
                <label className="flex items-center gap-2">
                  <span>Text</span>
                  <input
                    type="color"
                    value={exportOptions.textColor}
                    onChange={(event) => patchExportOptions({ textColor: event.target.value })}
                    className="h-7 w-full rounded border border-[rgba(255,255,255,0.14)] bg-transparent"
                  />
                </label>
              </div>
            </div>
          </UiPanel>
        </div>,
        document.body
      )}

      {/* Metadata summary */}
      <div className="mt-1 flex shrink-0 items-center gap-2 text-[10px] text-text-muted">
        <span>{framesWithImages}/{totalFrames} frames with images</span>
        {frameAspectRatio && <span>Aspect: {frameAspectRatio}</span>}
      </div>

      {exportError && <div className="mt-2 shrink-0 text-xs text-red-400">{exportError}</div>}

      {/* Frame context menu portal */}
      {frameContextMenu && typeof document !== 'undefined' && createPortal(
        <div
          ref={frameContextMenuRef}
          className="nowheel fixed z-[160] w-[140px] overflow-hidden rounded-lg border border-[rgba(255,255,255,0.16)] bg-surface-dark shadow-xl"
          style={{ left: `${frameContextMenu.x}px`, top: `${frameContextMenu.y}px` }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-text-dark hover:bg-[rgba(255,255,255,0.08)]"
            onClick={() => handleEditFrame(data.frames.find((f) => f.id === frameContextMenu.frameId)!)}
          >
            View Image
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-text-dark hover:bg-[rgba(255,255,255,0.08)]"
            disabled={frameContextMenu.frameIndex === 0}
            onClick={() => handleMoveFrame(frameContextMenu.frameId, 'up')}
          >
            Move Up
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-text-dark hover:bg-[rgba(255,255,255,0.08)]"
            disabled={frameContextMenu.frameIndex === totalFrames - 1}
            onClick={() => handleMoveFrame(frameContextMenu.frameId, 'down')}
          >
            Move Down
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-text-dark hover:bg-[rgba(255,255,255,0.08)]"
            onClick={() => handleDuplicateFrame(frameContextMenu.frameId)}
          >
            Duplicate
          </button>
          <div className="mx-2 h-px bg-[rgba(255,255,255,0.08)]" />
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-red-400 hover:bg-red-500/10"
            disabled={data.frames.length <= 1}
            onClick={() => handleDeleteFrame(frameContextMenu.frameId)}
          >
            Delete Frame
          </button>
        </div>,
        document.body
      )}

      <Handle type="target" id="target" position={Position.Left} className="!h-2 !w-2 !border-surface-dark !bg-accent" />
      <Handle type="source" id="source" position={Position.Right} className="!h-2 !w-2 !border-surface-dark !bg-accent" />
      <NodeResizeHandle minWidth={STORYBOARD_NODE_WIDTH_PX} minHeight={STORYBOARD_NODE_MIN_HEIGHT_PX} maxWidth={1800} maxHeight={1600} />
    </div>
  );
});

StoryboardNode.displayName = 'StoryboardNode';
