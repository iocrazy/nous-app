import {
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  memo,
  useMemo,
  useState,
  useCallback,
  useEffect,
  useRef,
} from 'react';
import { Handle, Position, useUpdateNodeInternals, useViewport } from '@xyflow/react';
import { Minus, Plus, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  AUTO_REQUEST_ASPECT_RATIO,
  CANVAS_NODE_TYPES,
  DEFAULT_ASPECT_RATIO,
  EXPORT_RESULT_NODE_DEFAULT_WIDTH,
  EXPORT_RESULT_NODE_LAYOUT_HEIGHT,
  type ImageSize,
  type StoryboardRatioControlMode,
  type StoryboardGenNodeData,
} from '../domain/canvasNodes';
import { EXPORT_RESULT_DISPLAY_NAME, resolveNodeDisplayName } from '../domain/nodeDisplay';
import { useCanvasStore } from '../../../stores/canvasStore';
import { useSettingsStore } from '../../../stores/settingsStore';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import {
  canvasAiGateway,
  canvasGraphImageResolver,
} from '../application/canvasServices';
import {
  detectAspectRatio,
  parseAspectRatio,
  resolveImageDisplayUrl,
} from '../application/imageData';
import {
  findReferenceTokens,
  insertReferenceToken,
  removeTextRange,
  resolveReferenceAwareDeleteRange,
} from '../application/referenceTokenEditing';
import {
  DEFAULT_IMAGE_MODEL_ID,
  getImageModel,
  listImageModels,
  resolveImageModelResolution,
  resolveImageModelResolutions,
} from '../models';
import { GRSAI_NANO_BANANA_PRO_MODEL_ID } from '../models/image/grsai/nanoBananaPro';
import { FAL_NANO_BANANA_2_MODEL_ID } from '../models/image/fal/nanoBanana2';
import { KIE_NANO_BANANA_2_MODEL_ID } from '../models/image/kie/nanoBanana2';
import { resolveModelPriceDisplay } from '../pricing';
import { ModelParamsControls } from '../ui/ModelParamsControls';
import { CanvasNodeImage } from '../ui/CanvasNodeImage';
import { UiButton } from '../../../components/ui';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '../ui/NodeHeader';
import { NodePriceBadge } from '../ui/NodePriceBadge';
import { NodeResizeHandle } from '../ui/NodeResizeHandle';
import {
  NODE_CONTROL_CHIP_CLASS,
  NODE_CONTROL_ICON_CLASS,
  NODE_CONTROL_MODEL_CHIP_CLASS,
  NODE_CONTROL_PARAMS_CHIP_CLASS,
  NODE_CONTROL_PRIMARY_BUTTON_CLASS,
} from '../ui/nodeControlStyles';

type StoryboardGenNodeProps = {
  id: string;
  data: StoryboardGenNodeData;
  selected?: boolean;
  width?: number;
  height?: number;
};

interface AspectRatioChoice {
  value: string;
  label: string;
}

interface PickerAnchor {
  left: number;
  top: number;
}

const AUTO_ASPECT_RATIO_OPTION: AspectRatioChoice = {
  value: AUTO_REQUEST_ASPECT_RATIO,
  label: 'Auto',
};
const PICKER_FALLBACK_ANCHOR: PickerAnchor = { left: 8, top: 8 };

const STORYBOARD_NODE_HORIZONTAL_PADDING_PX = 24;
const STORYBOARD_GRID_GAP_PX = 2;
const STORYBOARD_GRID_BASE_CELL_HEIGHT_PX = 78;
const STORYBOARD_GRID_MAX_WIDTH_PX = 320;
const STORYBOARD_CONTROL_ROW_WIDTH_PX = 274;
const STORYBOARD_PARAMS_ROW_WIDTH_PX = 286;
const STORYBOARD_GEN_NODE_MIN_WIDTH_PX = 200;
const STORYBOARD_GEN_NODE_MIN_HEIGHT_PX = 320;
const GRID_CONTROL_CONTAINER_CLASS = 'flex h-5 items-center gap-0.5 rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(255,255,255,0.04)] px-1';
const GRID_CONTROL_LABEL_CLASS = 'text-[9px] text-text-muted';
const GRID_CONTROL_BUTTON_CLASS = 'flex h-3 w-3 items-center justify-center rounded text-text-muted transition-colors hover:bg-white/10 hover:text-text-dark';
const GRID_CONTROL_ICON_CLASS = 'h-1.5 w-1.5';
const GRID_CONTROL_VALUE_CLASS = 'min-w-[14px] text-center text-[9px] font-semibold text-text-dark';
const GRID_SUMMARY_CLASS = 'flex h-5 items-center rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(255,255,255,0.05)] px-1.5 text-[9px] text-text-muted';
const FRAME_GRID_GAP_PX = 2;
const CONTROL_ROW_HEIGHT_PX = 20;
const CONTROL_ROW_MARGIN_BOTTOM_PX = 10;
const FRAME_GRID_MARGIN_BOTTOM_PX = 8;
const PARAM_ROW_HEIGHT_PX = 20;
const NODE_VERTICAL_PADDING_PX = 24;
const FRAME_CELL_MIN_WIDTH_PX = 24;
const FRAME_CELL_MIN_HEIGHT_PX = 16;
const FRIENDLY_ASPECT_RATIO_CANDIDATES = [
  '1:1', '16:9', '9:16', '4:3', '3:4', '21:9', '9:21', '3:2', '2:3', '5:4', '4:5',
];
const RATIO_CONTROL_MODE_BUTTON_CLASS =
  'flex h-5 items-center rounded-full border px-1.5 text-[9px] transition-colors';

function getTextareaCaretOffset(
  textarea: HTMLTextAreaElement,
  caretIndex: number
): PickerAnchor {
  const mirror = document.createElement('div');
  const computed = window.getComputedStyle(textarea);
  const mirrorStyle = mirror.style;
  mirrorStyle.position = 'absolute';
  mirrorStyle.visibility = 'hidden';
  mirrorStyle.pointerEvents = 'none';
  mirrorStyle.whiteSpace = 'pre-wrap';
  mirrorStyle.overflowWrap = 'break-word';
  mirrorStyle.wordBreak = 'break-word';
  mirrorStyle.boxSizing = computed.boxSizing;
  mirrorStyle.width = `${textarea.clientWidth}px`;
  mirrorStyle.font = computed.font;
  mirrorStyle.lineHeight = computed.lineHeight;
  mirrorStyle.letterSpacing = computed.letterSpacing;
  mirrorStyle.padding = computed.padding;
  mirrorStyle.border = computed.border;
  mirror.textContent = textarea.value.slice(0, caretIndex);
  const marker = document.createElement('span');
  marker.textContent = textarea.value.slice(caretIndex, caretIndex + 1) || ' ';
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const left = marker.offsetLeft - textarea.scrollLeft;
  const top = marker.offsetTop - textarea.scrollTop;
  document.body.removeChild(mirror);
  return { left: Math.max(0, left), top: Math.max(0, top) };
}

function resolvePickerAnchor(
  container: HTMLDivElement | null,
  textarea: HTMLTextAreaElement,
  caretIndex: number,
  zoom: number
): PickerAnchor {
  if (!container) return PICKER_FALLBACK_ANCHOR;
  const containerRect = container.getBoundingClientRect();
  const textareaRect = textarea.getBoundingClientRect();
  const caretOffset = getTextareaCaretOffset(textarea, caretIndex);
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  return {
    left: Math.max(0, (textareaRect.left - containerRect.left) / safeZoom + caretOffset.left),
    top: Math.max(0, (textareaRect.top - containerRect.top) / safeZoom + caretOffset.top),
  };
}

function resolvePointerAnchor(
  container: HTMLDivElement | null,
  clientX: number,
  clientY: number,
  zoom: number
): PickerAnchor {
  if (!container) return PICKER_FALLBACK_ANCHOR;
  const containerRect = container.getBoundingClientRect();
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  return {
    left: Math.max(0, (clientX - containerRect.left) / safeZoom),
    top: Math.max(0, (clientY - containerRect.top) / safeZoom),
  };
}

function renderFrameDescriptionWithHighlights(description: string, maxImageCount: number): ReactNode {
  if (!description) return ' ';
  const segments: ReactNode[] = [];
  let lastIndex = 0;
  const referenceTokens = findReferenceTokens(description, maxImageCount);
  for (const token of referenceTokens) {
    if (token.start > lastIndex) {
      segments.push(<span key={`plain-${lastIndex}`}>{description.slice(lastIndex, token.start)}</span>);
    }
    segments.push(
      <span
        key={`ref-${token.start}`}
        className="relative z-0 text-white [text-shadow:0.24px_0_currentColor,-0.24px_0_currentColor] before:absolute before:-inset-x-[4px] before:-inset-y-[1px] before:-z-10 before:rounded-[7px] before:bg-accent/55 before:content-['']"
      >
        {token.token}
      </span>
    );
    lastIndex = token.start + token.token.length;
  }
  if (lastIndex < description.length) {
    segments.push(<span key={`plain-${lastIndex}`}>{description.slice(lastIndex)}</span>);
  }
  return segments;
}

function buildFrameDescriptionDrafts(frames: StoryboardGenNodeData['frames']): Record<string, string> {
  const drafts: Record<string, string> = {};
  for (const frame of frames) { drafts[frame.id] = frame.description; }
  return drafts;
}

function areFrameDescriptionDraftsEqual(left: Record<string, string>, right: Record<string, string>): boolean {
  const leftEntries = Object.entries(left);
  if (leftEntries.length !== Object.keys(right).length) return false;
  return leftEntries.every(([key, value]) => right[key] === value);
}

function generateFrameId(): string {
  return `frame-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function toCssAspectRatio(aspectRatio: string): string {
  const [width = '1', height = '1'] = aspectRatio.split(':');
  return `${width} / ${height}`;
}

function pickClosestAspectRatio(targetRatio: number, supportedAspectRatios: string[]): string {
  const supported = supportedAspectRatios.length > 0 ? supportedAspectRatios : ['1:1'];
  let bestValue = supported[0];
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const ar of supported) {
    const distance = Math.abs(Math.log(parseAspectRatio(ar) / targetRatio));
    if (distance < bestDistance) { bestDistance = distance; bestValue = ar; }
  }
  return bestValue;
}

function ratioValueToAspectRatioString(ratioValue: number): string {
  if (!Number.isFinite(ratioValue) || ratioValue <= 0) return DEFAULT_ASPECT_RATIO;
  const scaledWidth = Math.max(1, Math.round(ratioValue * 1000));
  const scaledHeight = 1000;
  const gcd = (a: number, b: number): number => {
    let x = Math.abs(a); let y = Math.abs(b);
    while (y !== 0) { const t = y; y = x % y; x = t; }
    return x || 1;
  };
  const divisor = gcd(scaledWidth, scaledHeight);
  return `${Math.round(scaledWidth / divisor)}:${Math.round(scaledHeight / divisor)}`;
}

function formatFriendlyAspectRatio(ratioValue: number): string {
  if (!Number.isFinite(ratioValue) || ratioValue <= 0) return DEFAULT_ASPECT_RATIO;
  const snapped = pickClosestAspectRatio(ratioValue, FRIENDLY_ASPECT_RATIO_CANDIDATES);
  if (Math.abs(Math.log(parseAspectRatio(snapped) / ratioValue)) <= Math.log(1.04)) return snapped;
  return ratioValue >= 1 ? `${ratioValue.toFixed(2)}:1` : `1:${(1 / ratioValue).toFixed(2)}`;
}

function resolveStoryboardAspectRatios(
  mode: StoryboardRatioControlMode,
  controlRatioValue: number,
  rows: number,
  cols: number
) {
  const safeRows = Math.max(1, rows);
  const safeCols = Math.max(1, cols);
  const safeControl = Number.isFinite(controlRatioValue) && controlRatioValue > 0 ? controlRatioValue : 1;
  const cellRatioValue = mode === 'cell' ? safeControl : safeControl * (safeRows / safeCols);
  const overallRatioValue = mode === 'overall' ? safeControl : safeControl * (safeCols / safeRows);
  return {
    cellRatioValue,
    overallRatioValue,
    cellAspectRatio: ratioValueToAspectRatioString(cellRatioValue),
    overallAspectRatio: ratioValueToAspectRatioString(overallRatioValue),
    cellAspectRatioLabel: formatFriendlyAspectRatio(cellRatioValue),
    overallAspectRatioLabel: formatFriendlyAspectRatio(overallRatioValue),
  };
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
  const { t, i18n } = useTranslation();
  const { zoom } = useViewport();
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const addNode = useCanvasStore((state) => state.addNode);
  const addEdge = useCanvasStore((state) => state.addEdge);
  const findNodePosition = useCanvasStore((state) => state.findNodePosition);
  const currentProjectId = useStoryboardStore((state) => state.currentProjectId);
  const apiKeys = useSettingsStore((state) => state.apiKeys);
  const grsaiNanoBananaProModel = useSettingsStore((state) => state.grsaiNanoBananaProModel);
  const showNodePrice = useSettingsStore((state) => state.showNodePrice);
  const priceDisplayCurrencyMode = useSettingsStore((state) => state.priceDisplayCurrencyMode);
  const usdToCnyRate = useSettingsStore((state) => state.usdToCnyRate);
  const preferDiscountedPrice = useSettingsStore((state) => state.preferDiscountedPrice);
  const grsaiCreditTierId = useSettingsStore((state) => state.grsaiCreditTierId);

  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const activeFrameTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [showImagePicker, setShowImagePicker] = useState(false);
  const [pickerFrameIndex, setPickerFrameIndex] = useState<number | null>(null);
  const [pickerCursor, setPickerCursor] = useState<number | null>(null);
  const [pickerActiveIndex, setPickerActiveIndex] = useState(0);
  const [pickerAnchor, setPickerAnchor] = useState<PickerAnchor>(PICKER_FALLBACK_ANCHOR);
  const lastPointerAnchorRef = useRef<{ frameIndex: number; anchor: PickerAnchor } | null>(null);
  const frameTextareaRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});
  const frameHighlightRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const nodeData = data as StoryboardGenNodeData;
  const [frameDescriptionDrafts, setFrameDescriptionDrafts] = useState<Record<string, string>>(() =>
    buildFrameDescriptionDrafts(nodeData.frames)
  );
  const frameDescriptionDraftsRef = useRef(frameDescriptionDrafts);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.storyboardGen, nodeData),
    [nodeData]
  );

  const incomingImages = useMemo(
    () => canvasGraphImageResolver.collectInputImages(id, nodes, edges),
    [id, nodes, edges]
  );
  const incomingImageItems = useMemo(
    () => incomingImages.map((imageUrl, index) => ({
      imageUrl,
      displayUrl: resolveImageDisplayUrl(imageUrl),
      label: `Image ${index + 1}`,
    })),
    [incomingImages]
  );
  const incomingImageViewerList = useMemo(
    () => incomingImageItems.map((item) => resolveImageDisplayUrl(item.imageUrl)),
    [incomingImageItems]
  );

  const imageModels = useMemo(() => listImageModels(), []);
  const selectedModel = useMemo(() => {
    const modelId = nodeData.model ?? DEFAULT_IMAGE_MODEL_ID;
    return getImageModel(modelId);
  }, [nodeData.model]);
  const providerApiKey = apiKeys[selectedModel.providerId] ?? '';
  const effectiveExtraParams = useMemo(
    () => ({
      ...(nodeData.extraParams ?? {}),
      ...(selectedModel.id === GRSAI_NANO_BANANA_PRO_MODEL_ID
        ? { grsai_pro_model: grsaiNanoBananaProModel }
        : {}),
    }),
    [grsaiNanoBananaProModel, nodeData.extraParams, selectedModel.id]
  );
  const resolutionOptions = useMemo(
    () => resolveImageModelResolutions(selectedModel, { extraParams: effectiveExtraParams }),
    [effectiveExtraParams, selectedModel]
  );
  const selectedResolution = useMemo(
    () => resolveImageModelResolution(selectedModel, nodeData.size, { extraParams: effectiveExtraParams }),
    [effectiveExtraParams, nodeData.size, selectedModel]
  );
  const aspectRatioOptions = useMemo<AspectRatioChoice[]>(
    () => [AUTO_ASPECT_RATIO_OPTION, ...selectedModel.aspectRatios],
    [selectedModel.aspectRatios]
  );
  const selectedAspectRatio = useMemo((): AspectRatioChoice => {
    const found = nodeData.requestAspectRatio
      ? aspectRatioOptions.find((item) => item.value === nodeData.requestAspectRatio)
      : undefined;
    return found ?? AUTO_ASPECT_RATIO_OPTION;
  }, [aspectRatioOptions, nodeData.requestAspectRatio]);

  const showAdvancedRatioControls = false; // simplified for web
  const ratioControlMode: StoryboardRatioControlMode = nodeData.ratioControlMode === 'overall' ? 'overall' : 'cell';
  const controlAspectRatioValue = useMemo(() => {
    if (selectedAspectRatio.value === AUTO_REQUEST_ASPECT_RATIO) {
      return nodeData.aspectRatio || DEFAULT_ASPECT_RATIO;
    }
    return selectedAspectRatio.value || DEFAULT_ASPECT_RATIO;
  }, [nodeData.aspectRatio, selectedAspectRatio.value]);
  const resolvedAspectRatios = useMemo(
    () => resolveStoryboardAspectRatios(ratioControlMode, parseAspectRatio(controlAspectRatioValue), nodeData.gridRows, nodeData.gridCols),
    [controlAspectRatioValue, nodeData.gridCols, nodeData.gridRows, ratioControlMode]
  );
  const frameAspectRatioValue = resolvedAspectRatios.cellAspectRatio;

  const showWebSearchToggle =
    selectedModel.id === FAL_NANO_BANANA_2_MODEL_ID || selectedModel.id === KIE_NANO_BANANA_2_MODEL_ID;
  const webSearchEnabled = Boolean(nodeData.extraParams?.enable_web_search);

  const resolvedPriceDisplay = useMemo(
    () =>
      showNodePrice
        ? resolveModelPriceDisplay(selectedModel, {
          resolution: selectedResolution.value,
          extraParams: effectiveExtraParams,
          language: i18n.language,
          settings: { displayCurrencyMode: priceDisplayCurrencyMode, usdToCnyRate, preferDiscountedPrice, grsaiCreditTierId },
        })
        : null,
    [grsaiCreditTierId, i18n.language, preferDiscountedPrice, priceDisplayCurrencyMode, effectiveExtraParams, selectedModel, selectedResolution.value, showNodePrice, usdToCnyRate]
  );
  const resolvedPriceTooltip = useMemo(() => {
    if (!resolvedPriceDisplay) return undefined;
    const lines = [resolvedPriceDisplay.label];
    if (resolvedPriceDisplay.nativeLabel) lines.push(`Native: ${resolvedPriceDisplay.nativeLabel}`);
    if (resolvedPriceDisplay.originalLabel) lines.push(`Original: ${resolvedPriceDisplay.originalLabel}`);
    return lines.join('\n');
  }, [resolvedPriceDisplay]);

  const supportedAspectRatioValues = useMemo(
    () => selectedModel.aspectRatios.map((item) => item.value),
    [selectedModel.aspectRatios]
  );
  const mappedOverallRequestAspectRatio = useMemo(
    () => pickClosestAspectRatio(resolvedAspectRatios.overallRatioValue, supportedAspectRatioValues),
    [resolvedAspectRatios.overallRatioValue, supportedAspectRatioValues]
  );

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

  const totalFrames = useMemo(() => (nodeData.gridRows ?? 1) * (nodeData.gridCols ?? 1), [nodeData.gridRows, nodeData.gridCols]);
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
    const cellWidth = Math.max(FRAME_CELL_MIN_WIDTH_PX, Math.floor(Math.min(widthLimitedCellWidth, heightLimitedCellWidth)));
    const gridWidth = cols * cellWidth + Math.max(0, cols - 1) * STORYBOARD_GRID_GAP_PX;
    const paramsRowWidth = Math.max(STORYBOARD_PARAMS_ROW_WIDTH_PX, Math.floor(innerWidth));
    return { cellWidth, gridWidth, paramsRowWidth, cellAspectRatio: toCssAspectRatio(frameAspectRatioValue) };
  }, [frameAspectRatioValue, nodeData.gridCols, nodeData.gridRows, resolvedNodeHeight, resolvedNodeWidth]);

  useEffect(() => { frameDescriptionDraftsRef.current = frameDescriptionDrafts; }, [frameDescriptionDrafts]);

  useEffect(() => {
    const nextDrafts = buildFrameDescriptionDrafts(nodeData.frames);
    setFrameDescriptionDrafts((prev) => areFrameDescriptionDraftsEqual(prev, nextDrafts) ? prev : nextDrafts);
  }, [nodeData.frames]);

  useEffect(() => { updateNodeInternals(id); }, [id, resolvedNodeHeight, resolvedNodeWidth, updateNodeInternals]);

  // Sync model defaults
  useEffect(() => {
    if (nodeData.model !== selectedModel.id) updateNodeData(id, { model: selectedModel.id });
    if (nodeData.size !== selectedResolution.value) updateNodeData(id, { size: selectedResolution.value as ImageSize });
    if (nodeData.requestAspectRatio !== selectedAspectRatio.value) updateNodeData(id, { requestAspectRatio: selectedAspectRatio.value });
  }, [id, nodeData, selectedModel.id, selectedResolution.value, selectedAspectRatio.value, updateNodeData]);

  useEffect(() => {
    if (incomingImages.length === 0) {
      setShowImagePicker(false);
      setPickerFrameIndex(null);
      setPickerCursor(null);
      setPickerActiveIndex(0);
      return;
    }
    setPickerActiveIndex((prev) => Math.min(prev, incomingImages.length - 1));
  }, [incomingImages.length]);

  useEffect(() => {
    const handleOutsidePointerDown = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setShowImagePicker(false);
      setPickerFrameIndex(null);
      setPickerCursor(null);
    };
    document.addEventListener('pointerdown', handleOutsidePointerDown, true);
    return () => document.removeEventListener('pointerdown', handleOutsidePointerDown, true);
  }, []);

  // Auto-generate frames when grid changes
  useEffect(() => {
    if (nodeData.frames.length === totalFrames) return;
    const newFrames: StoryboardGenNodeData['frames'] = [];
    for (let i = 0; i < totalFrames; i++) {
      if (i < nodeData.frames.length) newFrames.push(nodeData.frames[i]);
      else newFrames.push({ id: generateFrameId(), description: '', referenceIndex: null });
    }
    updateNodeData(id, { frames: newFrames });
  }, [id, nodeData.frames, totalFrames, updateNodeData]);

  const handleRowChange = useCallback(
    (delta: number) => { updateNodeData(id, { gridRows: Math.max(1, Math.min(9, nodeData.gridRows + delta)) }); },
    [nodeData, updateNodeData, id]
  );

  const handleColChange = useCallback(
    (delta: number) => { updateNodeData(id, { gridCols: Math.max(1, Math.min(9, nodeData.gridCols + delta)) }); },
    [nodeData, updateNodeData, id]
  );

  const handleFrameDescriptionChange = useCallback(
    (index: number, description: string) => {
      const frame = nodeData.frames[index];
      if (!frame) return;
      setFrameDescriptionDrafts((prev) => prev[frame.id] === description ? prev : { ...prev, [frame.id]: description });
      const firstRef = findReferenceTokens(description, incomingImages.length)[0];
      const referenceIndex = firstRef ? firstRef.value - 1 : null;
      if (frame.description === description && frame.referenceIndex === referenceIndex) return;
      const newFrames = [...nodeData.frames];
      newFrames[index] = { ...frame, description, referenceIndex };
      updateNodeData(id, { frames: newFrames });
    },
    [id, incomingImages.length, nodeData.frames, updateNodeData]
  );

  const closeImagePicker = useCallback(() => {
    setShowImagePicker(false);
    setPickerFrameIndex(null);
    setPickerCursor(null);
    setPickerActiveIndex(0);
  }, []);

  const syncFrameHighlightScroll = useCallback((frameId: string) => {
    const textarea = frameTextareaRefs.current[frameId];
    const highlight = frameHighlightRefs.current[frameId];
    if (textarea && highlight) { highlight.scrollTop = textarea.scrollTop; highlight.scrollLeft = textarea.scrollLeft; }
  }, []);

  const insertImageReference = useCallback((imageIndex: number) => {
    if (!nodeData || pickerFrameIndex === null) return;
    const frame = nodeData.frames[pickerFrameIndex];
    if (!frame) { closeImagePicker(); return; }
    const marker = `@Image${imageIndex + 1}`;
    const currentDescription = frameDescriptionDraftsRef.current[frame.id] ?? frame.description;
    const cursor = pickerCursor ?? currentDescription.length;
    const { nextText, nextCursor } = insertReferenceToken(currentDescription, cursor, marker);
    handleFrameDescriptionChange(pickerFrameIndex, nextText);
    closeImagePicker();
    requestAnimationFrame(() => {
      activeFrameTextareaRef.current?.focus();
      activeFrameTextareaRef.current?.setSelectionRange(nextCursor, nextCursor);
    });
  }, [closeImagePicker, handleFrameDescriptionChange, nodeData, pickerCursor, pickerFrameIndex]);

  const handleFrameDescriptionKeyDown = useCallback(
    (index: number, event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (showImagePicker && incomingImages.length > 0 && pickerFrameIndex === index) {
        if (event.key === 'ArrowDown') { event.preventDefault(); setPickerActiveIndex((p) => (p + 1) % incomingImages.length); return; }
        if (event.key === 'ArrowUp') { event.preventDefault(); setPickerActiveIndex((p) => p === 0 ? incomingImages.length - 1 : p - 1); return; }
        if (event.key === 'Enter') { event.preventDefault(); insertImageReference(pickerActiveIndex); return; }
      }

      if (event.key === 'Backspace' || event.key === 'Delete') {
        const frame = nodeData.frames[index];
        if (!frame) return;
        const currentDescription = frameDescriptionDraftsRef.current[frame.id] ?? frame.description;
        const selStart = event.currentTarget.selectionStart ?? currentDescription.length;
        const selEnd = event.currentTarget.selectionEnd ?? selStart;
        const deleteRange = resolveReferenceAwareDeleteRange(currentDescription, selStart, selEnd, event.key === 'Backspace' ? 'backward' : 'forward', incomingImages.length);
        if (deleteRange) {
          event.preventDefault();
          const { nextText, nextCursor } = removeTextRange(currentDescription, deleteRange);
          handleFrameDescriptionChange(index, nextText);
          requestAnimationFrame(() => {
            activeFrameTextareaRef.current?.focus();
            activeFrameTextareaRef.current?.setSelectionRange(nextCursor, nextCursor);
            syncFrameHighlightScroll(frame.id);
          });
          return;
        }
      }

      if (event.key === '@' && incomingImages.length > 0) {
        event.preventDefault();
        const cursor = event.currentTarget.selectionStart ?? event.currentTarget.value.length;
        const pointerAnchor = lastPointerAnchorRef.current;
        if (pointerAnchor && pointerAnchor.frameIndex === index) {
          setPickerAnchor(pointerAnchor.anchor);
        } else {
          setPickerAnchor(resolvePickerAnchor(rootRef.current, event.currentTarget, cursor, zoom));
        }
        setPickerFrameIndex(index);
        setPickerCursor(cursor);
        setPickerActiveIndex(0);
        setShowImagePicker(true);
        activeFrameTextareaRef.current = event.currentTarget;
        return;
      }

      if (event.key === 'Escape' && showImagePicker) { event.preventDefault(); closeImagePicker(); }
    },
    [closeImagePicker, handleFrameDescriptionChange, incomingImages.length, insertImageReference, nodeData.frames, pickerActiveIndex, pickerFrameIndex, showImagePicker, syncFrameHighlightScroll, zoom]
  );

  const buildPrompt = useCallback((): string => {
    if (!nodeData) return '';
    const { gridRows, gridCols, frames } = nodeData;
    const parts: string[] = [`Generate a ${gridRows}x${gridCols} storyboard grid with ${gridRows * gridCols} frames.`];
    frames.forEach((frame, index) => {
      const desc = (frameDescriptionDraftsRef.current[frame.id] ?? frame.description).trim();
      if (!desc) return;
      parts.push(`Frame ${index + 1}: ${desc}`);
    });
    return parts.join('\n');
  }, [nodeData]);

  const handleGenerate = useCallback(async () => {
    if (!nodeData) return;
    if (!currentProjectId) { setError('No project context — save the project first'); return; }
    const prompt = buildPrompt();
    if (!prompt || nodeData.frames.every((f) => !(frameDescriptionDraftsRef.current[f.id] ?? f.description).trim())) {
      setError('Please add descriptions to at least one frame');
      return;
    }

    const generationStartedAt = Date.now();
    const newNodePosition = findNodePosition(id, EXPORT_RESULT_NODE_DEFAULT_WIDTH, EXPORT_RESULT_NODE_LAYOUT_HEIGHT);
    const newNodeId = addNode(CANVAS_NODE_TYPES.exportImage, newNodePosition, {
      isGenerating: true,
      generationStartedAt,
      generationDurationMs: selectedModel.expectedDurationMs ?? 60000,
      displayName: EXPORT_RESULT_DISPLAY_NAME.storyboardGenOutput,
      resultKind: 'storyboardGenOutput' as const,
      prompt: '',
      model: selectedModel.id,
      size: selectedResolution.value as ImageSize,
      requestAspectRatio: mappedOverallRequestAspectRatio,
    });
    addEdge(id, newNodeId);
    setSelectedNode(null);
    setError(null);

    try {
      const taskId = await canvasAiGateway.submitGenerateImageJob({
        projectId: currentProjectId,
        nodeId: id,
        prompt,
        model: nodeData.model || selectedModel.id,
        aspectRatio: mappedOverallRequestAspectRatio,
      });

      updateNodeData(newNodeId, {
        generationJobId: taskId,
        generationSourceType: 'storyboardGen',
        generationProviderId: selectedModel.providerId,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(`Generation failed: ${message}`);
      updateNodeData(newNodeId, {
        isGenerating: false,
        generationStartedAt: null,
        generationJobId: null,
        generationError: message,
      });
    }
  }, [addEdge, addNode, buildPrompt, currentProjectId, findNodePosition, id, mappedOverallRequestAspectRatio, nodeData, selectedModel, selectedResolution.value, setSelectedNode, updateNodeData]);

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
        rightSlot={
          resolvedPriceDisplay ? (
            <NodePriceBadge label={resolvedPriceDisplay.label} title={resolvedPriceTooltip} />
          ) : undefined
        }
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      {/* Frame summary + grid settings */}
      <div className="mb-2.5 flex shrink-0 items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <GridStepperControl label={t('node.storyboardGen.rowsShort', 'R')} value={nodeData.gridRows} onDecrease={() => handleRowChange(-1)} onIncrease={() => handleRowChange(1)} />
          <GridStepperControl label={t('node.storyboardGen.colsShort', 'C')} value={nodeData.gridCols} onDecrease={() => handleColChange(-1)} onIncrease={() => handleColChange(1)} />
        </div>

        {showAdvancedRatioControls && (
          <div className="min-w-0 flex-1 rounded-full border border-[rgba(255,255,255,0.12)] bg-[rgba(255,255,255,0.04)] px-2 py-0.5 text-center text-[10px] text-text-muted">
            <span>Cell: {resolvedAspectRatios.cellAspectRatioLabel}</span>
            <span className="mx-1 text-[rgba(255,255,255,0.22)]">|</span>
            <span>Overall: {resolvedAspectRatios.overallAspectRatioLabel}</span>
          </div>
        )}

        <div className="flex items-center gap-1">
          {showAdvancedRatioControls && (
            <div className="flex h-5 items-center rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(255,255,255,0.04)] p-0.5">
              <button type="button"
                className={`${RATIO_CONTROL_MODE_BUTTON_CLASS} ${ratioControlMode === 'overall' ? 'border-accent/55 bg-accent/18 text-text-dark' : 'border-transparent bg-transparent text-text-muted hover:bg-white/5'}`}
                onClick={(e) => { e.stopPropagation(); updateNodeData(id, { ratioControlMode: 'overall' }); }}
              >Overall</button>
              <button type="button"
                className={`${RATIO_CONTROL_MODE_BUTTON_CLASS} ${ratioControlMode === 'cell' ? 'border-accent/55 bg-accent/18 text-text-dark' : 'border-transparent bg-transparent text-text-muted hover:bg-white/5'}`}
                onClick={(e) => { e.stopPropagation(); updateNodeData(id, { ratioControlMode: 'cell' }); }}
              >Cell</button>
            </div>
          )}
          <div className={GRID_SUMMARY_CLASS}>
            {t('node.storyboardGen.frameCount', { count: totalFrames, defaultValue: `${totalFrames} frames` })}
          </div>
        </div>
      </div>

      {/* Frame Grid */}
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
                <div
                  ref={(el) => { frameHighlightRefs.current[frame.id] = el; }}
                  aria-hidden="true"
                  className="ui-scrollbar pointer-events-none absolute inset-0 overflow-y-auto overflow-x-hidden text-[10px] leading-4 text-text-dark"
                  style={{ scrollbarGutter: 'stable' }}
                >
                  <div className="min-h-full whitespace-pre-wrap break-words px-1.5 py-1 text-left">
                    {renderFrameDescriptionWithHighlights(frameDescription, incomingImages.length)}
                  </div>
                </div>
                <textarea
                  ref={(el) => { frameTextareaRefs.current[frame.id] = el; }}
                  value={frameDescription}
                  onChange={(event) => handleFrameDescriptionChange(index, event.target.value)}
                  onKeyDown={(event) => handleFrameDescriptionKeyDown(index, event)}
                  onScroll={() => syncFrameHighlightScroll(frame.id)}
                  onPointerDown={(event) => {
                    lastPointerAnchorRef.current = {
                      frameIndex: index,
                      anchor: resolvePointerAnchor(rootRef.current, event.clientX, event.clientY, zoom),
                    };
                  }}
                  onFocus={(event) => {
                    activeFrameTextareaRef.current = event.currentTarget;
                    syncFrameHighlightScroll(frame.id);
                  }}
                  placeholder={t('node.storyboardGen.framePlaceholder', { index: String(index + 1).padStart(2, '0'), defaultValue: `Frame ${String(index + 1).padStart(2, '0')}` })}
                  wrap="soft"
                  className="ui-scrollbar nodrag nowheel relative z-10 h-full w-full resize-none overflow-y-auto overflow-x-hidden bg-transparent px-1.5 py-1 text-left text-[10px] leading-4 text-transparent caret-text-dark placeholder:text-text-muted/40 focus:border-accent/50 focus:outline-none whitespace-pre-wrap break-words"
                  style={{ scrollbarGutter: 'stable' }}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* Image reference picker */}
      {showImagePicker && incomingImageItems.length > 0 && (
        <div
          className="nowheel absolute z-30 w-[120px] overflow-hidden rounded-xl border border-[rgba(255,255,255,0.16)] bg-surface-dark shadow-xl"
          style={{ left: pickerAnchor.left, top: pickerAnchor.top }}
          onMouseDown={(event) => event.stopPropagation()}
          onWheelCapture={(event) => event.stopPropagation()}
        >
          <div className="ui-scrollbar nowheel max-h-[180px] overflow-y-auto" onWheelCapture={(e) => e.stopPropagation()}>
            {incomingImageItems.map((item, imageIndex) => (
              <button
                key={`${item.imageUrl}-${imageIndex}`}
                type="button"
                onClick={(e) => { e.stopPropagation(); insertImageReference(imageIndex); }}
                onMouseEnter={() => setPickerActiveIndex(imageIndex)}
                className={`flex w-full items-center gap-2 border border-transparent bg-bg-dark/70 px-2 py-2 text-left text-sm text-text-dark transition-colors hover:border-[rgba(255,255,255,0.18)] ${pickerActiveIndex === imageIndex ? 'border-[rgba(255,255,255,0.24)] bg-bg-dark' : ''}`}
              >
                <CanvasNodeImage
                  src={item.displayUrl}
                  alt={item.label}
                  viewerSourceUrl={resolveImageDisplayUrl(item.imageUrl)}
                  viewerImageList={incomingImageViewerList}
                  className="h-8 w-8 rounded object-cover"
                />
                <span>{item.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {error && <div className="mb-1.5 shrink-0 text-[10px] text-red-400">{error}</div>}

      {/* AI Parameters */}
      <div
        className="relative mx-auto mt-auto flex shrink-0 items-center justify-between"
        style={{ width: `${frameLayout.paramsRowWidth}px` }}
      >
        <ModelParamsControls
          imageModels={imageModels}
          selectedModel={selectedModel}
          resolutionOptions={resolutionOptions}
          selectedResolution={selectedResolution}
          selectedAspectRatio={selectedAspectRatio}
          aspectRatioOptions={aspectRatioOptions}
          onModelChange={(modelId) => updateNodeData(id, { model: modelId })}
          onResolutionChange={(resolution) => updateNodeData(id, { size: resolution as ImageSize })}
          onAspectRatioChange={(ar) => updateNodeData(id, { requestAspectRatio: ar })}
          extraParams={nodeData.extraParams}
          onExtraParamChange={(key, value) =>
            updateNodeData(id, { extraParams: { ...(nodeData.extraParams ?? {}), [key]: value } })
          }
          showWebSearchToggle={showWebSearchToggle}
          webSearchEnabled={webSearchEnabled}
          onWebSearchToggle={(enabled) =>
            updateNodeData(id, { extraParams: { ...(nodeData.extraParams ?? {}), enable_web_search: enabled } })
          }
          triggerSize="sm"
          chipClassName={NODE_CONTROL_CHIP_CLASS}
          modelChipClassName={NODE_CONTROL_MODEL_CHIP_CLASS}
          paramsChipClassName={NODE_CONTROL_PARAMS_CHIP_CLASS}
          modelPanelAlign="center"
          paramsPanelAlign="center"
          modelPanelClassName="inline-block min-w-[300px] max-w-[calc(100vw-32px)] p-2"
          paramsPanelClassName="w-[420px] p-3"
        />

        <UiButton
          onClick={(event: ReactMouseEvent<HTMLButtonElement>) => {
            event.stopPropagation();
            void handleGenerate();
          }}
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
