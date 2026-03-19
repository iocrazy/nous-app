import {
  memo,
  useMemo,
  useState,
  useCallback,
  useEffect,
  useRef,
} from 'react';
import { Handle, Position, useUpdateNodeInternals } from '@xyflow/react';
import { Minus, Plus, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  CANVAS_NODE_TYPES,
  DEFAULT_ASPECT_RATIO,
  type StoryboardGenNodeData,
} from '../domain/canvasNodes';
import { resolveNodeDisplayName } from '../domain/nodeDisplay';
import { useCanvasStore } from '../../../stores/canvasStore';
import { parseAspectRatio } from '../application/imageData';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '../ui/NodeHeader';
import { NodeResizeHandle } from '../ui/NodeResizeHandle';
import { UiButton } from '../../../components/ui';
import {
  NODE_CONTROL_ICON_CLASS,
  NODE_CONTROL_PRIMARY_BUTTON_CLASS,
} from '../ui/nodeControlStyles';

type StoryboardGenNodeProps = {
  id: string;
  data: StoryboardGenNodeData;
  selected?: boolean;
  width?: number;
  height?: number;
};

const STORYBOARD_NODE_HORIZONTAL_PADDING_PX = 24;
const STORYBOARD_GRID_GAP_PX = 2;
const STORYBOARD_GRID_BASE_CELL_HEIGHT_PX = 78;
const STORYBOARD_GRID_MAX_WIDTH_PX = 320;
const STORYBOARD_CONTROL_ROW_WIDTH_PX = 274;
const STORYBOARD_PARAMS_ROW_WIDTH_PX = 286;
const STORYBOARD_GEN_NODE_MIN_WIDTH_PX = 200;
const STORYBOARD_GEN_NODE_MIN_HEIGHT_PX = 320;
const FRAME_GRID_GAP_PX = 2;
const CONTROL_ROW_HEIGHT_PX = 20;
const CONTROL_ROW_MARGIN_BOTTOM_PX = 10;
const FRAME_GRID_MARGIN_BOTTOM_PX = 8;
const PARAM_ROW_HEIGHT_PX = 20;
const NODE_VERTICAL_PADDING_PX = 24;
const FRAME_CELL_MIN_WIDTH_PX = 24;
const FRAME_CELL_MIN_HEIGHT_PX = 16;
const GRID_CONTROL_CONTAINER_CLASS = 'flex h-5 items-center gap-0.5 rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(255,255,255,0.04)] px-1';
const GRID_CONTROL_LABEL_CLASS = 'text-[9px] text-text-muted';
const GRID_CONTROL_BUTTON_CLASS = 'flex h-3 w-3 items-center justify-center rounded text-text-muted transition-colors hover:bg-white/10 hover:text-text-dark';
const GRID_CONTROL_ICON_CLASS = 'h-1.5 w-1.5';
const GRID_CONTROL_VALUE_CLASS = 'min-w-[14px] text-center text-[9px] font-semibold text-text-dark';
const GRID_SUMMARY_CLASS = 'flex h-5 items-center rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(255,255,255,0.05)] px-1.5 text-[9px] text-text-muted';

function generateFrameId(): string {
  return `frame-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function toCssAspectRatio(aspectRatio: string): string {
  const [width = '1', height = '1'] = aspectRatio.split(':');
  return `${width} / ${height}`;
}

type GridStepperControlProps = {
  label: string;
  value: number;
  onDecrease: () => void;
  onIncrease: () => void;
};

function GridStepperControl({ label, value, onDecrease, onIncrease }: GridStepperControlProps) {
  return (
    <div className={GRID_CONTROL_CONTAINER_CLASS}>
      <span className={GRID_CONTROL_LABEL_CLASS}>{label}</span>
      <button type="button" className={GRID_CONTROL_BUTTON_CLASS} onClick={(e) => { e.stopPropagation(); onDecrease(); }}>
        <Minus className={GRID_CONTROL_ICON_CLASS} />
      </button>
      <span className={GRID_CONTROL_VALUE_CLASS}>{value}</span>
      <button type="button" className={GRID_CONTROL_BUTTON_CLASS} onClick={(e) => { e.stopPropagation(); onIncrease(); }}>
        <Plus className={GRID_CONTROL_ICON_CLASS} />
      </button>
    </div>
  );
}

export const StoryboardGenNode = memo(({ id, data, selected, width, height }: StoryboardGenNodeProps) => {
  const { t } = useTranslation();
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const nodeData = data as StoryboardGenNodeData;
  const [frameDescriptionDrafts, setFrameDescriptionDrafts] = useState<Record<string, string>>(() => {
    const drafts: Record<string, string> = {};
    for (const frame of nodeData.frames) {
      drafts[frame.id] = frame.description;
    }
    return drafts;
  });

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.storyboardGen, nodeData),
    [nodeData]
  );

  const frameAspectRatioValue = nodeData.aspectRatio || DEFAULT_ASPECT_RATIO;

  const baseFrameLayout = useMemo(() => {
    const aspectRatio = Math.max(0.1, parseAspectRatio(frameAspectRatioValue));
    let cellWidth = STORYBOARD_GRID_BASE_CELL_HEIGHT_PX * aspectRatio;
    let gridWidth = nodeData.gridCols * cellWidth + Math.max(0, nodeData.gridCols - 1) * STORYBOARD_GRID_GAP_PX;
    if (gridWidth > STORYBOARD_GRID_MAX_WIDTH_PX) {
      const scale = STORYBOARD_GRID_MAX_WIDTH_PX / gridWidth;
      cellWidth *= scale;
      gridWidth = nodeData.gridCols * cellWidth + Math.max(0, nodeData.gridCols - 1) * STORYBOARD_GRID_GAP_PX;
    }
    const roundedCellWidth = Math.max(FRAME_CELL_MIN_WIDTH_PX, Math.round(cellWidth));
    const roundedCellHeight = Math.max(FRAME_CELL_MIN_HEIGHT_PX, Math.round(roundedCellWidth / aspectRatio));
    const roundedGridWidth = nodeData.gridCols * roundedCellWidth + Math.max(0, nodeData.gridCols - 1) * STORYBOARD_GRID_GAP_PX;
    const roundedGridHeight = nodeData.gridRows * roundedCellHeight + Math.max(0, nodeData.gridRows - 1) * FRAME_GRID_GAP_PX;
    const nodeInnerWidth = Math.max(STORYBOARD_CONTROL_ROW_WIDTH_PX, STORYBOARD_PARAMS_ROW_WIDTH_PX, roundedGridWidth);
    const nodeWidth = Math.max(STORYBOARD_GEN_NODE_MIN_WIDTH_PX, Math.round(nodeInnerWidth + STORYBOARD_NODE_HORIZONTAL_PADDING_PX));
    const nodeHeight = Math.max(STORYBOARD_GEN_NODE_MIN_HEIGHT_PX, Math.round(NODE_VERTICAL_PADDING_PX + CONTROL_ROW_HEIGHT_PX + CONTROL_ROW_MARGIN_BOTTOM_PX + roundedGridHeight + FRAME_GRID_MARGIN_BOTTOM_PX + PARAM_ROW_HEIGHT_PX));
    return { nodeWidth, nodeHeight };
  }, [frameAspectRatioValue, nodeData.gridCols, nodeData.gridRows]);

  const totalFrames = useMemo(
    () => (nodeData.gridRows ?? 1) * (nodeData.gridCols ?? 1),
    [nodeData.gridRows, nodeData.gridCols]
  );

  const resolvedNodeWidth = Math.max(baseFrameLayout.nodeWidth, Math.round(width ?? baseFrameLayout.nodeWidth));
  const resolvedNodeHeight = Math.max(baseFrameLayout.nodeHeight, Math.round(height ?? baseFrameLayout.nodeHeight));

  const frameLayout = useMemo(() => {
    const cols = Math.max(1, nodeData.gridCols);
    const rows = Math.max(1, nodeData.gridRows);
    const aspectRatio = Math.max(0.1, parseAspectRatio(frameAspectRatioValue));
    const innerWidth = Math.max(120, resolvedNodeWidth - STORYBOARD_NODE_HORIZONTAL_PADDING_PX);
    const availableGridHeight = Math.max(72, resolvedNodeHeight - NODE_VERTICAL_PADDING_PX - CONTROL_ROW_HEIGHT_PX - CONTROL_ROW_MARGIN_BOTTOM_PX - FRAME_GRID_MARGIN_BOTTOM_PX - PARAM_ROW_HEIGHT_PX);
    const widthLimitedCellWidth = (innerWidth - Math.max(0, cols - 1) * STORYBOARD_GRID_GAP_PX) / cols;
    const heightLimitedCellHeight = (availableGridHeight - Math.max(0, rows - 1) * FRAME_GRID_GAP_PX) / rows;
    const heightLimitedCellWidth = heightLimitedCellHeight * aspectRatio;
    const resolvedCellWidth = Math.floor(Math.min(widthLimitedCellWidth, heightLimitedCellWidth));
    const cellWidth = Math.max(FRAME_CELL_MIN_WIDTH_PX, resolvedCellWidth);
    const gridWidth = cols * cellWidth + Math.max(0, cols - 1) * STORYBOARD_GRID_GAP_PX;
    const paramsRowWidth = Math.max(STORYBOARD_PARAMS_ROW_WIDTH_PX, Math.floor(innerWidth));
    return { cellWidth, gridWidth, paramsRowWidth, cellAspectRatio: toCssAspectRatio(frameAspectRatioValue) };
  }, [frameAspectRatioValue, nodeData.gridCols, nodeData.gridRows, resolvedNodeHeight, resolvedNodeWidth]);

  useEffect(() => {
    const nextDrafts: Record<string, string> = {};
    for (const frame of nodeData.frames) { nextDrafts[frame.id] = frame.description; }
    setFrameDescriptionDrafts(nextDrafts);
  }, [nodeData.frames]);

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedNodeHeight, resolvedNodeWidth, updateNodeInternals]);

  // Auto-generate frames when grid changes
  useEffect(() => {
    const currentFrames = nodeData.frames;
    const targetCount = totalFrames;
    if (currentFrames.length === targetCount) return;
    const newFrames: StoryboardGenNodeData['frames'] = [];
    for (let i = 0; i < targetCount; i++) {
      if (i < currentFrames.length) {
        newFrames.push(currentFrames[i]);
      } else {
        newFrames.push({ id: generateFrameId(), description: '', referenceIndex: null });
      }
    }
    updateNodeData(id, { frames: newFrames });
  }, [id, nodeData.frames, totalFrames, updateNodeData]);

  const handleRowChange = useCallback(
    (delta: number) => {
      const newRows = Math.max(1, Math.min(9, nodeData.gridRows + delta));
      updateNodeData(id, { gridRows: newRows });
    },
    [nodeData, updateNodeData, id]
  );

  const handleColChange = useCallback(
    (delta: number) => {
      const newCols = Math.max(1, Math.min(9, nodeData.gridCols + delta));
      updateNodeData(id, { gridCols: newCols });
    },
    [nodeData, updateNodeData, id]
  );

  const handleFrameDescriptionChange = useCallback(
    (index: number, description: string) => {
      const frame = nodeData.frames[index];
      if (!frame) return;
      setFrameDescriptionDrafts((prev) => prev[frame.id] === description ? prev : { ...prev, [frame.id]: description });
      if (frame.description === description) return;
      const newFrames = [...nodeData.frames];
      newFrames[index] = { ...frame, description, referenceIndex: null };
      updateNodeData(id, { frames: newFrames });
    },
    [id, nodeData.frames, updateNodeData]
  );

  const handleGenerate = useCallback(async () => {
    // TODO: Phase 3 - AI integration
    setError('AI generation is not yet available (Phase 3)');
  }, []);

  if (!nodeData) return null;

  return (
    <div
      ref={rootRef}
      className={`
        group relative flex h-full flex-col overflow-visible rounded-[var(--node-radius)] border bg-surface-dark/95 p-3 transition-colors duration-150
        ${selected
          ? 'border-accent shadow-[0_0_0_1px_rgba(59,130,246,0.32)]'
          : 'border-[rgba(15,23,42,0.22)] hover:border-[rgba(15,23,42,0.34)] dark:border-[rgba(255,255,255,0.22)] dark:hover:border-[rgba(255,255,255,0.34)]'}
      `}
      style={{ width: `${resolvedNodeWidth}px`, height: `${resolvedNodeHeight}px` }}
      onClick={() => setSelectedNode(id)}
    >
      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<Sparkles className="h-4 w-4" />}
        titleText={resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <div className="mb-2.5 flex shrink-0 items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <GridStepperControl label={t('node.storyboardGen.rowsShort', 'R')} value={nodeData.gridRows} onDecrease={() => handleRowChange(-1)} onIncrease={() => handleRowChange(1)} />
          <GridStepperControl label={t('node.storyboardGen.colsShort', 'C')} value={nodeData.gridCols} onDecrease={() => handleColChange(-1)} onIncrease={() => handleColChange(1)} />
        </div>
        <div className={GRID_SUMMARY_CLASS}>
          {t('node.storyboardGen.frameCount', { count: totalFrames, defaultValue: `${totalFrames} frames` })}
        </div>
      </div>

      <div className="mb-2 flex min-h-0 flex-1 items-center justify-center">
        <div
          className="grid gap-0.5"
          style={{ width: `${frameLayout.gridWidth}px`, gridTemplateColumns: `repeat(${nodeData.gridCols}, ${frameLayout.cellWidth}px)` }}
        >
          {nodeData.frames.map((frame, index) => {
            const frameDescription = frameDescriptionDrafts[frame.id] ?? frame.description;
            return (
              <div
                key={frame.id}
                className="relative overflow-hidden rounded border border-[rgba(255,255,255,0.06)] bg-bg-dark/40"
                style={{ aspectRatio: frameLayout.cellAspectRatio }}
              >
                <textarea
                  value={frameDescription}
                  onChange={(event) => handleFrameDescriptionChange(index, event.target.value)}
                  onPointerDown={(event) => event.stopPropagation()}
                  placeholder={t('node.storyboardGen.framePlaceholder', { index: String(index + 1).padStart(2, '0'), defaultValue: `Frame ${String(index + 1).padStart(2, '0')}` })}
                  wrap="soft"
                  className="ui-scrollbar nodrag nowheel relative z-10 h-full w-full resize-none overflow-y-auto overflow-x-hidden bg-transparent px-1.5 py-1 text-left text-[10px] leading-4 text-text-dark caret-text-dark placeholder:text-text-muted/40 focus:border-accent/50 focus:outline-none whitespace-pre-wrap break-words"
                  style={{ scrollbarGutter: 'stable' }}
                />
              </div>
            );
          })}
        </div>
      </div>

      {error && <div className="mb-1.5 shrink-0 text-[10px] text-red-400">{error}</div>}

      <div
        className="relative mx-auto mt-auto flex shrink-0 items-center justify-between"
        style={{ width: `${frameLayout.paramsRowWidth}px` }}
      >
        {/* TODO: Phase 3 - ModelParamsControls */}
        <div className="flex-1" />
        <UiButton
          onClick={(event) => { event.stopPropagation(); void handleGenerate(); }}
          variant="primary"
          size="sm"
          className={`!min-w-0 shrink-0 ${NODE_CONTROL_PRIMARY_BUTTON_CLASS}`}
        >
          <Sparkles className={NODE_CONTROL_ICON_CLASS} strokeWidth={2.8} />
          {t('canvas.generate', 'Generate')}
        </UiButton>
      </div>

      <Handle type="target" id="target" position={Position.Left} className="!h-2 !w-2 !border-surface-dark !bg-accent" />
      <Handle type="source" id="source" position={Position.Right} className="!h-2 !w-2 !border-surface-dark !bg-accent" />
      <NodeResizeHandle minWidth={baseFrameLayout.nodeWidth} minHeight={baseFrameLayout.nodeHeight} maxWidth={1800} maxHeight={1400} />
    </div>
  );
});

StoryboardGenNode.displayName = 'StoryboardGenNode';
