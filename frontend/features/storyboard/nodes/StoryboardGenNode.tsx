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
import { Layers, Minus, Plus, Sparkles } from 'lucide-react';
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
import { FrameList } from './storyboard-gen/FrameList';

// ─── Types ──────────────────────────────────────────────────────────────────

type StoryboardGenNodeProps = {
  id: string;
  data: StoryboardGenNodeData;
  selected?: boolean;
  width?: number;
  height?: number;
};

interface AspectRatioChoice { value: string; label: string; }
interface PickerAnchor { left: number; top: number; }

// ─── Constants ──────────────────────────────────────────────────────────────

const AUTO_ASPECT_RATIO_OPTION: AspectRatioChoice = { value: AUTO_REQUEST_ASPECT_RATIO, label: 'Auto' };
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
const RATIO_CONTROL_MODE_BUTTON_CLASS = 'flex h-5 items-center rounded-full border px-1.5 text-[9px] transition-colors';
const COMPACT_BUTTON_CLASS = 'flex h-5 items-center rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(255,255,255,0.04)] px-1.5 text-[9px] text-text-muted hover:bg-white/10 transition-colors';

// ─── Utility functions ──────────────────────────────────────────────────────

function getTextareaCaretOffset(textarea: HTMLTextAreaElement, caretIndex: number): PickerAnchor {
  const mirror = document.createElement('div');
  const computed = window.getComputedStyle(textarea);
  const s = mirror.style;
  s.position = 'absolute'; s.visibility = 'hidden'; s.pointerEvents = 'none';
  s.whiteSpace = 'pre-wrap'; s.overflowWrap = 'break-word'; s.wordBreak = 'break-word';
  s.boxSizing = computed.boxSizing; s.width = `${textarea.clientWidth}px`;
  s.font = computed.font; s.lineHeight = computed.lineHeight;
  s.letterSpacing = computed.letterSpacing; s.padding = computed.padding;
  s.border = computed.border;
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

function resolvePickerAnchor(container: HTMLDivElement | null, textarea: HTMLTextAreaElement, caretIndex: number, zoom: number): PickerAnchor {
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

function resolvePointerAnchor(container: HTMLDivElement | null, clientX: number, clientY: number, zoom: number): PickerAnchor {
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
  for (const token of findReferenceTokens(description, maxImageCount)) {
    if (token.start > lastIndex) segments.push(<span key={`p-${lastIndex}`}>{description.slice(lastIndex, token.start)}</span>);
    segments.push(
      <span key={`r-${token.start}`} className="relative z-0 text-white [text-shadow:0.24px_0_currentColor,-0.24px_0_currentColor] before:absolute before:-inset-x-[4px] before:-inset-y-[1px] before:-z-10 before:rounded-[7px] before:bg-accent/55 before:content-['']">
        {token.token}
      </span>,
    );
    lastIndex = token.start + token.token.length;
  }
  if (lastIndex < description.length) segments.push(<span key={`p-${lastIndex}`}>{description.slice(lastIndex)}</span>);
  return segments;
}

function buildFrameDescriptionDrafts(frames: StoryboardGenNodeData['frames']): Record<string, string> {
  const drafts: Record<string, string> = {};
  for (const frame of frames) drafts[frame.id] = frame.description;
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
  const [w = '1', h = '1'] = aspectRatio.split(':');
  return `${w} / ${h}`;
}

function pickClosestAspectRatio(targetRatio: number, supported: string[]): string {
  const list = supported.length > 0 ? supported : ['1:1'];
  let best = list[0]; let bestDist = Infinity;
  for (const ar of list) {
    const d = Math.abs(Math.log(parseAspectRatio(ar) / targetRatio));
    if (d < bestDist) { bestDist = d; best = ar; }
  }
  return best;
}

function ratioValueToString(v: number): string {
  if (!Number.isFinite(v) || v <= 0) return DEFAULT_ASPECT_RATIO;
  const sw = Math.max(1, Math.round(v * 1000)); const sh = 1000;
  const gcd = (a: number, b: number): number => { let x = Math.abs(a); let y = Math.abs(b); while (y) { const t = y; y = x % y; x = t; } return x || 1; };
  const d = gcd(sw, sh);
  return `${Math.round(sw / d)}:${Math.round(sh / d)}`;
}

function formatFriendlyAspectRatio(v: number): string {
  if (!Number.isFinite(v) || v <= 0) return DEFAULT_ASPECT_RATIO;
  const snapped = pickClosestAspectRatio(v, FRIENDLY_ASPECT_RATIO_CANDIDATES);
  if (Math.abs(Math.log(parseAspectRatio(snapped) / v)) <= Math.log(1.04)) return snapped;
  return v >= 1 ? `${v.toFixed(2)}:1` : `1:${(1 / v).toFixed(2)}`;
}

function resolveStoryboardAspectRatios(mode: StoryboardRatioControlMode, control: number, rows: number, cols: number) {
  const sr = Math.max(1, rows); const sc = Math.max(1, cols);
  const sv = Number.isFinite(control) && control > 0 ? control : 1;
  const cell = mode === 'cell' ? sv : sv * (sr / sc);
  const overall = mode === 'overall' ? sv : sv * (sc / sr);
  return {
    cellRatioValue: cell, overallRatioValue: overall,
    cellAspectRatio: ratioValueToString(cell), overallAspectRatio: ratioValueToString(overall),
    cellAspectRatioLabel: formatFriendlyAspectRatio(cell), overallAspectRatioLabel: formatFriendlyAspectRatio(overall),
  };
}

// ─── Grid Stepper ───────────────────────────────────────────────────────────

function GridStepperControl({ label, value, onDecrease, onIncrease }: { label: string; value: number; onDecrease: () => void; onIncrease: () => void }) {
  return (
    <div className={GRID_CONTROL_CONTAINER_CLASS}>
      <span className={GRID_CONTROL_LABEL_CLASS}>{label}</span>
      <button type="button" className={GRID_CONTROL_BUTTON_CLASS} onClick={(e) => { e.stopPropagation(); onDecrease(); }}><Minus className={GRID_CONTROL_ICON_CLASS} /></button>
      <span className={GRID_CONTROL_VALUE_CLASS}>{value}</span>
      <button type="button" className={GRID_CONTROL_BUTTON_CLASS} onClick={(e) => { e.stopPropagation(); onIncrease(); }}><Plus className={GRID_CONTROL_ICON_CLASS} /></button>
    </div>
  );
}

// ─── Main component ─────────────────────────────────────────────────────────

export const StoryboardGenNode = memo(({ id, data, selected, width, height }: StoryboardGenNodeProps) => {
  const { t, i18n } = useTranslation();
  const { zoom } = useViewport();
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((s) => s.setSelectedNode);
  const nodes = useCanvasStore((s) => s.nodes);
  const edges = useCanvasStore((s) => s.edges);
  const updateNodeData = useCanvasStore((s) => s.updateNodeData);
  const addNode = useCanvasStore((s) => s.addNode);
  const addEdge = useCanvasStore((s) => s.addEdge);
  const findNodePosition = useCanvasStore((s) => s.findNodePosition);
  const currentProjectId = useStoryboardStore((s) => s.currentProjectId);
  const apiKeys = useSettingsStore((s) => s.apiKeys);
  const grsaiNanoBananaProModel = useSettingsStore((s) => s.grsaiNanoBananaProModel);
  const showNodePrice = useSettingsStore((s) => s.showNodePrice);
  const priceDisplayCurrencyMode = useSettingsStore((s) => s.priceDisplayCurrencyMode);
  const usdToCnyRate = useSettingsStore((s) => s.usdToCnyRate);
  const preferDiscountedPrice = useSettingsStore((s) => s.preferDiscountedPrice);
  const grsaiCreditTierId = useSettingsStore((s) => s.grsaiCreditTierId);

  const [error, setError] = useState<string | null>(null);
  const [compact, setCompact] = useState(false);
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
    buildFrameDescriptionDrafts(nodeData.frames),
  );
  const frameDescriptionDraftsRef = useRef(frameDescriptionDrafts);
  const resolvedTitle = useMemo(() => resolveNodeDisplayName(CANVAS_NODE_TYPES.storyboardGen, nodeData), [nodeData]);

  // ─── Incoming images ────────────────────────────────────────────────

  const incomingImages = useMemo(() => canvasGraphImageResolver.collectInputImages(id, nodes, edges), [id, nodes, edges]);
  const incomingImageItems = useMemo(() => incomingImages.map((url, i) => ({
    imageUrl: url, displayUrl: resolveImageDisplayUrl(url), label: `Image ${i + 1}`,
  })), [incomingImages]);
  const incomingImageViewerList = useMemo(() => incomingImageItems.map((i) => resolveImageDisplayUrl(i.imageUrl)), [incomingImageItems]);

  // ─── Model / resolution ─────────────────────────────────────────────

  const imageModels = useMemo(() => listImageModels(), []);
  const selectedModel = useMemo(() => getImageModel(nodeData.model ?? DEFAULT_IMAGE_MODEL_ID), [nodeData.model]);
  const providerApiKey = apiKeys[selectedModel.providerId] ?? '';
  const effectiveExtraParams = useMemo(() => ({
    ...(nodeData.extraParams ?? {}),
    ...(selectedModel.id === GRSAI_NANO_BANANA_PRO_MODEL_ID ? { grsai_pro_model: grsaiNanoBananaProModel } : {}),
  }), [grsaiNanoBananaProModel, nodeData.extraParams, selectedModel.id]);
  const resolutionOptions = useMemo(() => resolveImageModelResolutions(selectedModel, { extraParams: effectiveExtraParams }), [effectiveExtraParams, selectedModel]);
  const selectedResolution = useMemo(() => resolveImageModelResolution(selectedModel, nodeData.size, { extraParams: effectiveExtraParams }), [effectiveExtraParams, nodeData.size, selectedModel]);
  const aspectRatioOptions = useMemo<AspectRatioChoice[]>(() => [AUTO_ASPECT_RATIO_OPTION, ...selectedModel.aspectRatios], [selectedModel.aspectRatios]);
  const selectedAspectRatio = useMemo(() => {
    const found = nodeData.requestAspectRatio ? aspectRatioOptions.find((i) => i.value === nodeData.requestAspectRatio) : undefined;
    return found ?? AUTO_ASPECT_RATIO_OPTION;
  }, [aspectRatioOptions, nodeData.requestAspectRatio]);

  const showAdvancedRatioControls = false;
  const ratioControlMode: StoryboardRatioControlMode = nodeData.ratioControlMode === 'overall' ? 'overall' : 'cell';
  const controlAspectRatioValue = useMemo(() => selectedAspectRatio.value === AUTO_REQUEST_ASPECT_RATIO ? (nodeData.aspectRatio || DEFAULT_ASPECT_RATIO) : (selectedAspectRatio.value || DEFAULT_ASPECT_RATIO), [nodeData.aspectRatio, selectedAspectRatio.value]);
  const resolvedAspectRatios = useMemo(() => resolveStoryboardAspectRatios(ratioControlMode, parseAspectRatio(controlAspectRatioValue), nodeData.gridRows, nodeData.gridCols), [controlAspectRatioValue, nodeData.gridCols, nodeData.gridRows, ratioControlMode]);
  const frameAspectRatioValue = resolvedAspectRatios.cellAspectRatio;
  const showWebSearchToggle = selectedModel.id === FAL_NANO_BANANA_2_MODEL_ID || selectedModel.id === KIE_NANO_BANANA_2_MODEL_ID;
  const webSearchEnabled = Boolean(nodeData.extraParams?.enable_web_search);

  const resolvedPriceDisplay = useMemo(() => showNodePrice ? resolveModelPriceDisplay(selectedModel, { resolution: selectedResolution.value, extraParams: effectiveExtraParams, language: i18n.language, settings: { displayCurrencyMode: priceDisplayCurrencyMode, usdToCnyRate, preferDiscountedPrice, grsaiCreditTierId } }) : null, [grsaiCreditTierId, i18n.language, preferDiscountedPrice, priceDisplayCurrencyMode, effectiveExtraParams, selectedModel, selectedResolution.value, showNodePrice, usdToCnyRate]);
  const resolvedPriceTooltip = useMemo(() => {
    if (!resolvedPriceDisplay) return undefined;
    const lines = [resolvedPriceDisplay.label];
    if (resolvedPriceDisplay.nativeLabel) lines.push(`Native: ${resolvedPriceDisplay.nativeLabel}`);
    if (resolvedPriceDisplay.originalLabel) lines.push(`Original: ${resolvedPriceDisplay.originalLabel}`);
    return lines.join('\n');
  }, [resolvedPriceDisplay]);

  const supportedAspectRatioValues = useMemo(() => selectedModel.aspectRatios.map((i) => i.value), [selectedModel.aspectRatios]);
  const mappedOverallRequestAspectRatio = useMemo(() => pickClosestAspectRatio(resolvedAspectRatios.overallRatioValue, supportedAspectRatioValues), [resolvedAspectRatios.overallRatioValue, supportedAspectRatioValues]);

  // ─── Layout computation ─────────────────────────────────────────────

  const baseFrameLayout = useMemo(() => {
    const ar = Math.max(0.1, parseAspectRatio(frameAspectRatioValue));
    let cw = STORYBOARD_GRID_BASE_CELL_HEIGHT_PX * ar;
    let gw = nodeData.gridCols * cw + Math.max(0, nodeData.gridCols - 1) * STORYBOARD_GRID_GAP_PX;
    if (gw > STORYBOARD_GRID_MAX_WIDTH_PX) { const s = STORYBOARD_GRID_MAX_WIDTH_PX / gw; cw *= s; gw = nodeData.gridCols * cw + Math.max(0, nodeData.gridCols - 1) * STORYBOARD_GRID_GAP_PX; }
    const rcw = Math.max(FRAME_CELL_MIN_WIDTH_PX, Math.round(cw));
    const rch = Math.max(FRAME_CELL_MIN_HEIGHT_PX, Math.round(rcw / ar));
    const rgw = nodeData.gridCols * rcw + Math.max(0, nodeData.gridCols - 1) * STORYBOARD_GRID_GAP_PX;
    const rgh = nodeData.gridRows * rch + Math.max(0, nodeData.gridRows - 1) * FRAME_GRID_GAP_PX;
    const niw = Math.max(STORYBOARD_CONTROL_ROW_WIDTH_PX, STORYBOARD_PARAMS_ROW_WIDTH_PX, rgw);
    return {
      nodeWidth: Math.max(STORYBOARD_GEN_NODE_MIN_WIDTH_PX, Math.round(niw + STORYBOARD_NODE_HORIZONTAL_PADDING_PX)),
      nodeHeight: Math.max(STORYBOARD_GEN_NODE_MIN_HEIGHT_PX, Math.round(NODE_VERTICAL_PADDING_PX + CONTROL_ROW_HEIGHT_PX + CONTROL_ROW_MARGIN_BOTTOM_PX + rgh + FRAME_GRID_MARGIN_BOTTOM_PX + PARAM_ROW_HEIGHT_PX)),
    };
  }, [frameAspectRatioValue, nodeData.gridCols, nodeData.gridRows]);

  const totalFrames = useMemo(() => (nodeData.gridRows ?? 1) * (nodeData.gridCols ?? 1), [nodeData.gridRows, nodeData.gridCols]);
  const filledFrameCount = useMemo(() => nodeData.frames.filter((f) => (frameDescriptionDrafts[f.id] ?? f.description).trim().length > 0).length, [frameDescriptionDrafts, nodeData.frames]);
  const resolvedNodeWidth = Math.max(baseFrameLayout.nodeWidth, Math.round(width ?? baseFrameLayout.nodeWidth));
  const resolvedNodeHeight = Math.max(baseFrameLayout.nodeHeight, Math.round(height ?? baseFrameLayout.nodeHeight));

  const frameLayout = useMemo(() => {
    const cols = Math.max(1, nodeData.gridCols); const rows = Math.max(1, nodeData.gridRows);
    const ar = Math.max(0.1, parseAspectRatio(frameAspectRatioValue));
    const innerW = Math.max(120, resolvedNodeWidth - STORYBOARD_NODE_HORIZONTAL_PADDING_PX);
    const availH = Math.max(72, resolvedNodeHeight - NODE_VERTICAL_PADDING_PX - CONTROL_ROW_HEIGHT_PX - CONTROL_ROW_MARGIN_BOTTOM_PX - FRAME_GRID_MARGIN_BOTTOM_PX - PARAM_ROW_HEIGHT_PX);
    const wLim = (innerW - Math.max(0, cols - 1) * STORYBOARD_GRID_GAP_PX) / cols;
    const hLim = (availH - Math.max(0, rows - 1) * FRAME_GRID_GAP_PX) / rows;
    const cw = Math.max(FRAME_CELL_MIN_WIDTH_PX, Math.floor(Math.min(wLim, hLim * ar)));
    return {
      cellWidth: cw,
      gridWidth: cols * cw + Math.max(0, cols - 1) * STORYBOARD_GRID_GAP_PX,
      paramsRowWidth: Math.max(STORYBOARD_PARAMS_ROW_WIDTH_PX, Math.floor(innerW)),
      cellAspectRatio: toCssAspectRatio(frameAspectRatioValue),
    };
  }, [frameAspectRatioValue, nodeData.gridCols, nodeData.gridRows, resolvedNodeHeight, resolvedNodeWidth]);

  // ─── Effects ────────────────────────────────────────────────────────

  useEffect(() => { frameDescriptionDraftsRef.current = frameDescriptionDrafts; }, [frameDescriptionDrafts]);
  useEffect(() => {
    const next = buildFrameDescriptionDrafts(nodeData.frames);
    setFrameDescriptionDrafts((prev) => areFrameDescriptionDraftsEqual(prev, next) ? prev : next);
  }, [nodeData.frames]);
  useEffect(() => { updateNodeInternals(id); }, [id, resolvedNodeHeight, resolvedNodeWidth, updateNodeInternals]);
  useEffect(() => {
    if (nodeData.model !== selectedModel.id) updateNodeData(id, { model: selectedModel.id });
    if (nodeData.size !== selectedResolution.value) updateNodeData(id, { size: selectedResolution.value as ImageSize });
    if (nodeData.requestAspectRatio !== selectedAspectRatio.value) updateNodeData(id, { requestAspectRatio: selectedAspectRatio.value });
  }, [id, nodeData, selectedModel.id, selectedResolution.value, selectedAspectRatio.value, updateNodeData]);
  useEffect(() => {
    if (incomingImages.length === 0) { setShowImagePicker(false); setPickerFrameIndex(null); setPickerCursor(null); setPickerActiveIndex(0); return; }
    setPickerActiveIndex((p) => Math.min(p, incomingImages.length - 1));
  }, [incomingImages.length]);
  useEffect(() => {
    const handler = (e: PointerEvent) => { if (!rootRef.current?.contains(e.target as Node)) { setShowImagePicker(false); setPickerFrameIndex(null); setPickerCursor(null); } };
    document.addEventListener('pointerdown', handler, true);
    return () => document.removeEventListener('pointerdown', handler, true);
  }, []);
  useEffect(() => {
    if (nodeData.frames.length === totalFrames) return;
    const newFrames: StoryboardGenNodeData['frames'] = [];
    for (let i = 0; i < totalFrames; i++) {
      if (i < nodeData.frames.length) newFrames.push(nodeData.frames[i]);
      else newFrames.push({ id: generateFrameId(), description: '', referenceIndex: null });
    }
    updateNodeData(id, { frames: newFrames });
  }, [id, nodeData.frames, totalFrames, updateNodeData]);

  // ─── Callbacks ──────────────────────────────────────────────────────

  const handleRowChange = useCallback((d: number) => { updateNodeData(id, { gridRows: Math.max(1, Math.min(9, nodeData.gridRows + d)) }); }, [id, nodeData, updateNodeData]);
  const handleColChange = useCallback((d: number) => { updateNodeData(id, { gridCols: Math.max(1, Math.min(9, nodeData.gridCols + d)) }); }, [id, nodeData, updateNodeData]);

  const handleFrameDescriptionChange = useCallback((index: number, description: string) => {
    const frame = nodeData.frames[index];
    if (!frame) return;
    setFrameDescriptionDrafts((prev) => prev[frame.id] === description ? prev : { ...prev, [frame.id]: description });
    const firstRef = findReferenceTokens(description, incomingImages.length)[0];
    const refIndex = firstRef ? firstRef.value - 1 : null;
    if (frame.description === description && frame.referenceIndex === refIndex) return;
    const newFrames = [...nodeData.frames];
    newFrames[index] = { ...frame, description, referenceIndex: refIndex };
    updateNodeData(id, { frames: newFrames });
  }, [id, incomingImages.length, nodeData.frames, updateNodeData]);

  const closeImagePicker = useCallback(() => { setShowImagePicker(false); setPickerFrameIndex(null); setPickerCursor(null); setPickerActiveIndex(0); }, []);
  const syncFrameHighlightScroll = useCallback((frameId: string) => {
    const ta = frameTextareaRefs.current[frameId]; const hl = frameHighlightRefs.current[frameId];
    if (ta && hl) { hl.scrollTop = ta.scrollTop; hl.scrollLeft = ta.scrollLeft; }
  }, []);

  const insertImageReference = useCallback((imgIndex: number) => {
    if (!nodeData || pickerFrameIndex === null) return;
    const frame = nodeData.frames[pickerFrameIndex];
    if (!frame) { closeImagePicker(); return; }
    const marker = `@Image${imgIndex + 1}`;
    const cur = frameDescriptionDraftsRef.current[frame.id] ?? frame.description;
    const cursor = pickerCursor ?? cur.length;
    const { nextText, nextCursor } = insertReferenceToken(cur, cursor, marker);
    handleFrameDescriptionChange(pickerFrameIndex, nextText);
    closeImagePicker();
    requestAnimationFrame(() => { activeFrameTextareaRef.current?.focus(); activeFrameTextareaRef.current?.setSelectionRange(nextCursor, nextCursor); });
  }, [closeImagePicker, handleFrameDescriptionChange, nodeData, pickerCursor, pickerFrameIndex]);

  const handleFrameDescriptionKeyDown = useCallback((index: number, event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (showImagePicker && incomingImages.length > 0 && pickerFrameIndex === index) {
      if (event.key === 'ArrowDown') { event.preventDefault(); setPickerActiveIndex((p) => (p + 1) % incomingImages.length); return; }
      if (event.key === 'ArrowUp') { event.preventDefault(); setPickerActiveIndex((p) => p === 0 ? incomingImages.length - 1 : p - 1); return; }
      if (event.key === 'Enter') { event.preventDefault(); insertImageReference(pickerActiveIndex); return; }
    }
    if (event.key === 'Backspace' || event.key === 'Delete') {
      const frame = nodeData.frames[index]; if (!frame) return;
      const cur = frameDescriptionDraftsRef.current[frame.id] ?? frame.description;
      const ss = event.currentTarget.selectionStart ?? cur.length; const se = event.currentTarget.selectionEnd ?? ss;
      const dr = resolveReferenceAwareDeleteRange(cur, ss, se, event.key === 'Backspace' ? 'backward' : 'forward', incomingImages.length);
      if (dr) {
        event.preventDefault();
        const { nextText, nextCursor } = removeTextRange(cur, dr);
        handleFrameDescriptionChange(index, nextText);
        requestAnimationFrame(() => { activeFrameTextareaRef.current?.focus(); activeFrameTextareaRef.current?.setSelectionRange(nextCursor, nextCursor); syncFrameHighlightScroll(frame.id); });
        return;
      }
    }
    if (event.key === '@' && incomingImages.length > 0) {
      event.preventDefault();
      const cursor = event.currentTarget.selectionStart ?? event.currentTarget.value.length;
      const pa = lastPointerAnchorRef.current;
      setPickerAnchor(pa && pa.frameIndex === index ? pa.anchor : resolvePickerAnchor(rootRef.current, event.currentTarget, cursor, zoom));
      setPickerFrameIndex(index); setPickerCursor(cursor); setPickerActiveIndex(0); setShowImagePicker(true);
      activeFrameTextareaRef.current = event.currentTarget;
      return;
    }
    if (event.key === 'Escape' && showImagePicker) { event.preventDefault(); closeImagePicker(); }
  }, [closeImagePicker, handleFrameDescriptionChange, incomingImages.length, insertImageReference, nodeData.frames, pickerActiveIndex, pickerFrameIndex, showImagePicker, syncFrameHighlightScroll, zoom]);

  // ─── Frame reorder ──────────────────────────────────────────────────

  const handleFrameReorder = useCallback((fromIndex: number, toIndex: number) => {
    const newFrames = [...nodeData.frames];
    const [moved] = newFrames.splice(fromIndex, 1);
    newFrames.splice(toIndex, 0, moved);
    updateNodeData(id, { frames: newFrames });
  }, [id, nodeData.frames, updateNodeData]);

  // ─── Generate ───────────────────────────────────────────────────────

  const buildPrompt = useCallback((): string => {
    if (!nodeData) return '';
    const { gridRows, gridCols, frames } = nodeData;
    const parts: string[] = [`Generate a ${gridRows}x${gridCols} storyboard grid with ${gridRows * gridCols} frames.`];
    frames.forEach((frame, i) => {
      const desc = (frameDescriptionDraftsRef.current[frame.id] ?? frame.description).trim();
      if (desc) parts.push(`Frame ${i + 1}: ${desc}`);
    });
    return parts.join('\n');
  }, [nodeData]);

  const handleGenerate = useCallback(async () => {
    if (!nodeData) return;
    if (!currentProjectId) { setError('No project context — save the project first'); return; }
    const prompt = buildPrompt();
    if (!prompt || nodeData.frames.every((f) => !(frameDescriptionDraftsRef.current[f.id] ?? f.description).trim())) {
      setError('Please add descriptions to at least one frame'); return;
    }
    const startedAt = Date.now();
    const pos = findNodePosition(id, EXPORT_RESULT_NODE_DEFAULT_WIDTH, EXPORT_RESULT_NODE_LAYOUT_HEIGHT);
    const newId = addNode(CANVAS_NODE_TYPES.exportImage, pos, {
      isGenerating: true, generationStartedAt: startedAt, generationDurationMs: selectedModel.expectedDurationMs ?? 60000,
      displayName: EXPORT_RESULT_DISPLAY_NAME.storyboardGenOutput, resultKind: 'storyboardGenOutput' as const,
      prompt: '', model: selectedModel.id, size: selectedResolution.value as ImageSize, requestAspectRatio: mappedOverallRequestAspectRatio,
    });
    addEdge(id, newId); setSelectedNode(null); setError(null);
    try {
      const taskId = await canvasAiGateway.submitGenerateImageJob({ projectId: currentProjectId, nodeId: id, prompt, model: nodeData.model || selectedModel.id, aspectRatio: mappedOverallRequestAspectRatio });
      updateNodeData(newId, { generationJobId: taskId, generationSourceType: 'storyboardGen', generationProviderId: selectedModel.providerId });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Generation failed: ${msg}`);
      updateNodeData(newId, { isGenerating: false, generationStartedAt: null, generationJobId: null, generationError: msg });
    }
  }, [addEdge, addNode, buildPrompt, currentProjectId, findNodePosition, id, mappedOverallRequestAspectRatio, nodeData, selectedModel, selectedResolution.value, setSelectedNode, updateNodeData]);

  // ─── Render highlight helper ────────────────────────────────────────

  const renderHighlight = useCallback((desc: string) => renderFrameDescriptionWithHighlights(desc, incomingImages.length), [incomingImages.length]);

  if (!nodeData) return null;

  return (
    <div
      ref={rootRef}
      className={`group relative flex h-full flex-col overflow-visible rounded-[var(--node-radius)] border bg-surface-dark/95 p-3 transition-colors duration-150 ${
        selected ? 'border-accent shadow-[0_0_0_1px_rgba(59,130,246,0.32)]' : 'border-[rgba(15,23,42,0.22)] hover:border-[rgba(15,23,42,0.34)] dark:border-[rgba(255,255,255,0.22)] dark:hover:border-[rgba(255,255,255,0.34)]'
      }`}
      style={{ width: `${resolvedNodeWidth}px`, height: `${resolvedNodeHeight}px` }}
      onClick={() => setSelectedNode(id)}
    >
      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<Sparkles className="h-4 w-4" />}
        titleText={resolvedTitle}
        rightSlot={resolvedPriceDisplay ? <NodePriceBadge label={resolvedPriceDisplay.label} title={resolvedPriceTooltip} /> : undefined}
        editable
        onTitleChange={(t) => updateNodeData(id, { displayName: t })}
      />

      {/* Grid settings + frame counter */}
      <div className="mb-2.5 flex shrink-0 items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <GridStepperControl label={t('node.storyboardGen.rowsShort', 'R')} value={nodeData.gridRows} onDecrease={() => handleRowChange(-1)} onIncrease={() => handleRowChange(1)} />
          <GridStepperControl label={t('node.storyboardGen.colsShort', 'C')} value={nodeData.gridCols} onDecrease={() => handleColChange(-1)} onIncrease={() => handleColChange(1)} />
        </div>

        <div className="flex items-center gap-1">
          {/* Frame counter badge */}
          <div className={GRID_SUMMARY_CLASS}>
            {filledFrameCount}/{totalFrames} frames
          </div>
          {/* Compact mode toggle */}
          <button type="button" className={`${COMPACT_BUTTON_CLASS} ${compact ? 'bg-indigo-600/20 border-indigo-400/40 text-text-dark' : ''}`}
            onClick={(e) => { e.stopPropagation(); setCompact((p) => !p); }}
            title={compact ? 'Expanded view' : 'Compact view'}
          ><Layers className="h-2.5 w-2.5" /></button>
          {/* Copy/Paste */}
          <button type="button" className={COMPACT_BUTTON_CLASS}
            onClick={async (e) => { e.stopPropagation(); const text = nodeData.frames.map((f, i) => `Frame ${String(i + 1).padStart(2, '0')}: ${(frameDescriptionDraftsRef.current[f.id] ?? f.description).trim()}`).join('\n'); try { await navigator.clipboard.writeText(text); } catch { /* ignore */ } }}
            title="Copy all frame descriptions">Copy</button>
          <button type="button" className={COMPACT_BUTTON_CLASS}
            onClick={async (e) => { e.stopPropagation(); try { const text = await navigator.clipboard.readText(); text.split('\n').map((l) => l.replace(/^Frame\s*\d+\s*:\s*/i, '').trim()).forEach((l, i) => { if (i < nodeData.frames.length && l) handleFrameDescriptionChange(i, l); }); } catch { /* ignore */ } }}
            title="Paste frame descriptions">Paste</button>
        </div>
      </div>

      {/* Frame Grid — delegated to FrameList */}
      <div className="mb-2 flex min-h-0 flex-1 items-center justify-center">
        <FrameList
          frames={nodeData.frames}
          gridCols={nodeData.gridCols}
          frameDescriptionDrafts={frameDescriptionDrafts}
          cellWidth={frameLayout.cellWidth}
          gridWidth={frameLayout.gridWidth}
          cellAspectRatio={frameLayout.cellAspectRatio}
          incomingImageCount={incomingImages.length}
          renderHighlight={renderHighlight}
          onDescriptionChange={handleFrameDescriptionChange}
          onKeyDown={handleFrameDescriptionKeyDown}
          onReorder={handleFrameReorder}
          onPointerDown={(index, e) => {
            lastPointerAnchorRef.current = {
              frameIndex: index,
              anchor: resolvePointerAnchor(rootRef.current, e.clientX, e.clientY, zoom),
            };
          }}
          onFocus={(index, e) => {
            activeFrameTextareaRef.current = e.currentTarget;
            syncFrameHighlightScroll(nodeData.frames[index]?.id ?? '');
          }}
          onScroll={syncFrameHighlightScroll}
          highlightRefs={frameHighlightRefs}
          textareaRefs={frameTextareaRefs}
          compact={compact}
        />
      </div>

      {/* Image reference picker */}
      {showImagePicker && incomingImageItems.length > 0 && (
        <div className="nowheel absolute z-30 w-[120px] overflow-hidden rounded-xl border border-[rgba(255,255,255,0.16)] bg-surface-dark shadow-xl"
          style={{ left: pickerAnchor.left, top: pickerAnchor.top }}
          onMouseDown={(e) => e.stopPropagation()} onWheelCapture={(e) => e.stopPropagation()}>
          <div className="ui-scrollbar nowheel max-h-[180px] overflow-y-auto" onWheelCapture={(e) => e.stopPropagation()}>
            {incomingImageItems.map((item, imgIdx) => (
              <button key={`${item.imageUrl}-${imgIdx}`} type="button"
                onClick={(e) => { e.stopPropagation(); insertImageReference(imgIdx); }}
                onMouseEnter={() => setPickerActiveIndex(imgIdx)}
                className={`flex w-full items-center gap-2 border border-transparent bg-bg-dark/70 px-2 py-2 text-left text-sm text-text-dark transition-colors hover:border-[rgba(255,255,255,0.18)] ${pickerActiveIndex === imgIdx ? 'border-[rgba(255,255,255,0.24)] bg-bg-dark' : ''}`}>
                <CanvasNodeImage src={item.displayUrl} alt={item.label} viewerSourceUrl={resolveImageDisplayUrl(item.imageUrl)} viewerImageList={incomingImageViewerList} className="h-8 w-8 rounded object-cover" />
                <span>{item.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {error && <div className="mb-1.5 shrink-0 text-[10px] text-red-400">{error}</div>}

      {/* AI Parameters */}
      <div className="relative mx-auto mt-auto flex shrink-0 items-center justify-between" style={{ width: `${frameLayout.paramsRowWidth}px` }}>
        <ModelParamsControls
          imageModels={imageModels} selectedModel={selectedModel} resolutionOptions={resolutionOptions}
          selectedResolution={selectedResolution} selectedAspectRatio={selectedAspectRatio} aspectRatioOptions={aspectRatioOptions}
          onModelChange={(m) => updateNodeData(id, { model: m })}
          onResolutionChange={(r) => updateNodeData(id, { size: r as ImageSize })}
          onAspectRatioChange={(ar) => updateNodeData(id, { requestAspectRatio: ar })}
          extraParams={nodeData.extraParams}
          onExtraParamChange={(k, v) => updateNodeData(id, { extraParams: { ...(nodeData.extraParams ?? {}), [k]: v } })}
          showWebSearchToggle={showWebSearchToggle} webSearchEnabled={webSearchEnabled}
          onWebSearchToggle={(en) => updateNodeData(id, { extraParams: { ...(nodeData.extraParams ?? {}), enable_web_search: en } })}
          triggerSize="sm" chipClassName={NODE_CONTROL_CHIP_CLASS} modelChipClassName={NODE_CONTROL_MODEL_CHIP_CLASS}
          paramsChipClassName={NODE_CONTROL_PARAMS_CHIP_CLASS} modelPanelAlign="center" paramsPanelAlign="center"
          modelPanelClassName="inline-block min-w-[300px] max-w-[calc(100vw-32px)] p-2" paramsPanelClassName="w-[420px] p-3"
        />
        <UiButton onClick={(e: ReactMouseEvent<HTMLButtonElement>) => { e.stopPropagation(); void handleGenerate(); }}
          variant="primary" size="sm" className={`!min-w-0 shrink-0 ${NODE_CONTROL_PRIMARY_BUTTON_CLASS}`}>
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
