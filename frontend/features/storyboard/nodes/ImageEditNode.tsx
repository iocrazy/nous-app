import {
  type KeyboardEvent,
  type ReactNode,
  memo,
  useMemo,
  useState,
  useCallback,
  useEffect,
  useRef,
} from 'react';
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react';
import { Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  AUTO_REQUEST_ASPECT_RATIO,
  CANVAS_NODE_TYPES,
  EXPORT_RESULT_NODE_DEFAULT_WIDTH,
  EXPORT_RESULT_NODE_LAYOUT_HEIGHT,
  type ImageEditNodeData,
  type ImageSize,
} from '../domain/canvasNodes';
import { resolveNodeDisplayName } from '../domain/nodeDisplay';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '../ui/NodeHeader';
import { NodeResizeHandle } from '../ui/NodeResizeHandle';
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
import {
  NODE_CONTROL_CHIP_CLASS,
  NODE_CONTROL_ICON_CLASS,
  NODE_CONTROL_MODEL_CHIP_CLASS,
  NODE_CONTROL_PARAMS_CHIP_CLASS,
  NODE_CONTROL_PRIMARY_BUTTON_CLASS,
} from '../ui/nodeControlStyles';
import { ModelParamsControls } from '../ui/ModelParamsControls';
import { CanvasNodeImage } from '../ui/CanvasNodeImage';
import { NodePriceBadge } from '../ui/NodePriceBadge';
import { UiButton } from '../../../components/ui';
import { useCanvasStore } from '../../../stores/canvasStore';
import { useSettingsStore } from '../../../stores/settingsStore';
import { useStoryboardStore } from '../../../stores/storyboardStore';

type ImageEditNodeProps = NodeProps & {
  id: string;
  data: ImageEditNodeData;
  selected?: boolean;
};

interface AspectRatioChoice {
  value: string;
  label: string;
}

interface PickerAnchor {
  left: number;
  top: number;
}

const PICKER_FALLBACK_ANCHOR: PickerAnchor = { left: 8, top: 8 };
const PICKER_Y_OFFSET_PX = 20;
const IMAGE_EDIT_NODE_MIN_WIDTH = 390;
const IMAGE_EDIT_NODE_MIN_HEIGHT = 180;
const IMAGE_EDIT_NODE_MAX_WIDTH = 1400;
const IMAGE_EDIT_NODE_MAX_HEIGHT = 1000;
const IMAGE_EDIT_NODE_DEFAULT_WIDTH = 520;
const IMAGE_EDIT_NODE_DEFAULT_HEIGHT = 320;

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

function resolvePickerAnchor(container: HTMLDivElement | null, textarea: HTMLTextAreaElement, caretIndex: number): PickerAnchor {
  if (!container) return PICKER_FALLBACK_ANCHOR;
  const containerRect = container.getBoundingClientRect();
  const textareaRect = textarea.getBoundingClientRect();
  const caretOffset = getTextareaCaretOffset(textarea, caretIndex);
  return {
    left: Math.max(0, textareaRect.left - containerRect.left + caretOffset.left),
    top: Math.max(0, textareaRect.top - containerRect.top + caretOffset.top + PICKER_Y_OFFSET_PX),
  };
}

function renderPromptWithHighlights(prompt: string, maxImageCount: number): ReactNode {
  if (!prompt) return ' ';
  const segments: ReactNode[] = [];
  let lastIndex = 0;
  const referenceTokens = findReferenceTokens(prompt, maxImageCount);
  for (const token of referenceTokens) {
    if (token.start > lastIndex) {
      segments.push(<span key={`plain-${lastIndex}`}>{prompt.slice(lastIndex, token.start)}</span>);
    }
    segments.push(
      <span key={`ref-${token.start}`}
        className="relative z-0 text-white [text-shadow:0.24px_0_currentColor,-0.24px_0_currentColor] before:absolute before:-inset-x-[4px] before:-inset-y-[1px] before:-z-10 before:rounded-[7px] before:bg-accent/55 before:content-['']"
      >{token.token}</span>
    );
    lastIndex = token.start + token.token.length;
  }
  if (lastIndex < prompt.length) {
    segments.push(<span key={`plain-${lastIndex}`}>{prompt.slice(lastIndex)}</span>);
  }
  return segments;
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

export const ImageEditNode = memo(({ id, data, selected, width, height }: ImageEditNodeProps) => {
  const { t, i18n } = useTranslation();
  const updateNodeInternals = useUpdateNodeInternals();
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const promptHighlightRef = useRef<HTMLDivElement>(null);
  const [promptDraft, setPromptDraft] = useState(() => data.prompt ?? '');
  const promptDraftRef = useRef(promptDraft);
  const [showImagePicker, setShowImagePicker] = useState(false);
  const [pickerCursor, setPickerCursor] = useState<number | null>(null);
  const [pickerActiveIndex, setPickerActiveIndex] = useState(0);
  const [pickerAnchor, setPickerAnchor] = useState<PickerAnchor>(PICKER_FALLBACK_ANCHOR);

  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const addNode = useCanvasStore((state) => state.addNode);
  const findNodePosition = useCanvasStore((state) => state.findNodePosition);
  const addEdge = useCanvasStore((state) => state.addEdge);
  const currentProjectId = useStoryboardStore((state) => state.currentProjectId);
  const apiKeys = useSettingsStore((state) => state.apiKeys);
  const grsaiNanoBananaProModel = useSettingsStore((state) => state.grsaiNanoBananaProModel);
  const showNodePrice = useSettingsStore((state) => state.showNodePrice);
  const priceDisplayCurrencyMode = useSettingsStore((state) => state.priceDisplayCurrencyMode);
  const usdToCnyRate = useSettingsStore((state) => state.usdToCnyRate);
  const preferDiscountedPrice = useSettingsStore((state) => state.preferDiscountedPrice);
  const grsaiCreditTierId = useSettingsStore((state) => state.grsaiCreditTierId);

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
  const selectedModel = useMemo(() => getImageModel(data.model ?? DEFAULT_IMAGE_MODEL_ID), [data.model]);
  const providerApiKey = apiKeys[selectedModel.providerId] ?? '';
  const effectiveExtraParams = useMemo(
    () => ({
      ...(data.extraParams ?? {}),
      ...(selectedModel.id === GRSAI_NANO_BANANA_PRO_MODEL_ID ? { grsai_pro_model: grsaiNanoBananaProModel } : {}),
    }),
    [data.extraParams, grsaiNanoBananaProModel, selectedModel.id]
  );
  const resolutionOptions = useMemo(
    () => resolveImageModelResolutions(selectedModel, { extraParams: effectiveExtraParams }),
    [effectiveExtraParams, selectedModel]
  );
  const selectedResolution = useMemo(
    () => resolveImageModelResolution(selectedModel, data.size, { extraParams: effectiveExtraParams }),
    [data.size, effectiveExtraParams, selectedModel]
  );
  const aspectRatioOptions = useMemo<AspectRatioChoice[]>(
    () => [{ value: AUTO_REQUEST_ASPECT_RATIO, label: 'Auto' }, ...selectedModel.aspectRatios],
    [selectedModel.aspectRatios]
  );
  const selectedAspectRatio = useMemo(
    () => aspectRatioOptions.find((item) => item.value === data.requestAspectRatio) ?? aspectRatioOptions[0],
    [aspectRatioOptions, data.requestAspectRatio]
  );
  const showWebSearchToggle = selectedModel.id === FAL_NANO_BANANA_2_MODEL_ID || selectedModel.id === KIE_NANO_BANANA_2_MODEL_ID;
  const webSearchEnabled = Boolean(data.extraParams?.enable_web_search);
  const supportedAspectRatioValues = useMemo(() => selectedModel.aspectRatios.map((i) => i.value), [selectedModel.aspectRatios]);

  const resolvedPriceDisplay = useMemo(
    () => showNodePrice ? resolveModelPriceDisplay(selectedModel, {
      resolution: selectedResolution.value, extraParams: effectiveExtraParams, language: i18n.language,
      settings: { displayCurrencyMode: priceDisplayCurrencyMode, usdToCnyRate, preferDiscountedPrice, grsaiCreditTierId },
    }) : null,
    [grsaiCreditTierId, i18n.language, preferDiscountedPrice, priceDisplayCurrencyMode, effectiveExtraParams, selectedModel, selectedResolution.value, showNodePrice, usdToCnyRate]
  );
  const resolvedPriceTooltip = useMemo(() => {
    if (!resolvedPriceDisplay) return undefined;
    const lines = [resolvedPriceDisplay.label];
    if (resolvedPriceDisplay.nativeLabel) lines.push(`Native: ${resolvedPriceDisplay.nativeLabel}`);
    if (resolvedPriceDisplay.originalLabel) lines.push(`Original: ${resolvedPriceDisplay.originalLabel}`);
    return lines.join('\n');
  }, [resolvedPriceDisplay]);

  const resolvedTitle = useMemo(() => resolveNodeDisplayName(CANVAS_NODE_TYPES.imageEdit, data), [data]);
  const resolvedWidth = Math.max(IMAGE_EDIT_NODE_MIN_WIDTH, Math.round(width ?? IMAGE_EDIT_NODE_DEFAULT_WIDTH));
  const resolvedHeight = Math.max(IMAGE_EDIT_NODE_MIN_HEIGHT, Math.round(height ?? IMAGE_EDIT_NODE_DEFAULT_HEIGHT));

  useEffect(() => { updateNodeInternals(id); }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);
  useEffect(() => {
    const ext = data.prompt ?? '';
    if (ext !== promptDraftRef.current) { promptDraftRef.current = ext; setPromptDraft(ext); }
  }, [data.prompt]);

  const commitPromptDraft = useCallback((next: string) => { promptDraftRef.current = next; updateNodeData(id, { prompt: next }); }, [id, updateNodeData]);

  useEffect(() => {
    if (data.model !== selectedModel.id) updateNodeData(id, { model: selectedModel.id });
    if (data.size !== selectedResolution.value) updateNodeData(id, { size: selectedResolution.value as ImageSize });
    if (data.requestAspectRatio !== selectedAspectRatio.value) updateNodeData(id, { requestAspectRatio: selectedAspectRatio.value });
  }, [data.model, data.requestAspectRatio, data.size, id, selectedAspectRatio.value, selectedModel.id, selectedResolution.value, updateNodeData]);

  useEffect(() => {
    if (incomingImages.length === 0) { setShowImagePicker(false); setPickerCursor(null); setPickerActiveIndex(0); return; }
    setPickerActiveIndex((p) => Math.min(p, incomingImages.length - 1));
  }, [incomingImages.length]);

  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as globalThis.Node)) return;
      setShowImagePicker(false); setPickerCursor(null);
    };
    document.addEventListener('mousedown', handleOutside, true);
    return () => document.removeEventListener('mousedown', handleOutside, true);
  }, []);

  const handleGenerate = useCallback(async () => {
    const prompt = promptDraft.replace(/@(?=Image\d+)/g, '').trim();
    if (!prompt) { setError(t('node.imageEdit.promptRequired', 'Please enter a prompt')); return; }
    if (!currentProjectId) { setError('No project context — save the project first'); return; }

    const generationStartedAt = Date.now();
    const newNodePosition = findNodePosition(id, EXPORT_RESULT_NODE_DEFAULT_WIDTH, EXPORT_RESULT_NODE_LAYOUT_HEIGHT);
    const newNodeId = addNode(CANVAS_NODE_TYPES.exportImage, newNodePosition, {
      isGenerating: true,
      generationStartedAt,
      generationDurationMs: selectedModel.expectedDurationMs ?? 60000,
      resultKind: 'generic' as const,
      displayName: prompt.slice(0, 60) || 'AI Generated',
    });
    addEdge(id, newNodeId);
    setError(null);

    try {
      let resolvedRequestAspectRatio = selectedAspectRatio.value;
      if (resolvedRequestAspectRatio === AUTO_REQUEST_ASPECT_RATIO) {
        if (incomingImages.length > 0) {
          try {
            const src = await detectAspectRatio(incomingImages[0]);
            resolvedRequestAspectRatio = pickClosestAspectRatio(parseAspectRatio(src), supportedAspectRatioValues);
          } catch { resolvedRequestAspectRatio = pickClosestAspectRatio(1, supportedAspectRatioValues); }
        } else {
          resolvedRequestAspectRatio = pickClosestAspectRatio(1, supportedAspectRatioValues);
        }
      }

      const taskId = await canvasAiGateway.submitGenerateImageJob({
        projectId: currentProjectId,
        nodeId: id,
        prompt,
        model: data.model || selectedModel.id,
        aspectRatio: resolvedRequestAspectRatio,
      });
      updateNodeData(newNodeId, {
        generationJobId: taskId,
        generationSourceType: 'imageEdit',
        generationProviderId: selectedModel.providerId,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(`Generation failed: ${message}`);
      updateNodeData(newNodeId, {
        isGenerating: false, generationStartedAt: null, generationJobId: null,
        generationError: message,
      });
    }
  }, [addNode, addEdge, currentProjectId, data.model, findNodePosition, id, incomingImages, promptDraft, selectedAspectRatio.value, selectedModel, selectedResolution.value, supportedAspectRatioValues, t, updateNodeData]);

  const syncPromptHighlightScroll = () => {
    if (promptRef.current && promptHighlightRef.current) {
      promptHighlightRef.current.scrollTop = promptRef.current.scrollTop;
      promptHighlightRef.current.scrollLeft = promptRef.current.scrollLeft;
    }
  };

  const insertImageReference = useCallback((imageIndex: number) => {
    const marker = `@Image${imageIndex + 1}`;
    const current = promptDraftRef.current;
    const cursor = pickerCursor ?? current.length;
    const { nextText, nextCursor } = insertReferenceToken(current, cursor, marker);
    setPromptDraft(nextText); commitPromptDraft(nextText);
    setShowImagePicker(false); setPickerCursor(null); setPickerActiveIndex(0);
    requestAnimationFrame(() => {
      promptRef.current?.focus();
      promptRef.current?.setSelectionRange(nextCursor, nextCursor);
      syncPromptHighlightScroll();
    });
  }, [commitPromptDraft, pickerCursor]);

  const handlePromptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Backspace' || event.key === 'Delete') {
      const current = promptDraftRef.current;
      const selStart = event.currentTarget.selectionStart ?? current.length;
      const selEnd = event.currentTarget.selectionEnd ?? selStart;
      const deleteRange = resolveReferenceAwareDeleteRange(current, selStart, selEnd, event.key === 'Backspace' ? 'backward' : 'forward', incomingImages.length);
      if (deleteRange) {
        event.preventDefault();
        const { nextText, nextCursor } = removeTextRange(current, deleteRange);
        setPromptDraft(nextText); commitPromptDraft(nextText);
        requestAnimationFrame(() => {
          promptRef.current?.focus();
          promptRef.current?.setSelectionRange(nextCursor, nextCursor);
          syncPromptHighlightScroll();
        });
        return;
      }
    }

    if (showImagePicker && incomingImages.length > 0) {
      if (event.key === 'ArrowDown') { event.preventDefault(); setPickerActiveIndex((p) => (p + 1) % incomingImages.length); return; }
      if (event.key === 'ArrowUp') { event.preventDefault(); setPickerActiveIndex((p) => p === 0 ? incomingImages.length - 1 : p - 1); return; }
      if (event.key === 'Enter') { event.preventDefault(); insertImageReference(pickerActiveIndex); return; }
    }

    if (event.key === '@' && incomingImages.length > 0) {
      event.preventDefault();
      const cursor = event.currentTarget.selectionStart ?? promptDraftRef.current.length;
      setPickerAnchor(resolvePickerAnchor(rootRef.current, event.currentTarget, cursor));
      setPickerCursor(cursor); setShowImagePicker(true); setPickerActiveIndex(0);
      return;
    }
    if (event.key === 'Escape' && showImagePicker) { event.preventDefault(); setShowImagePicker(false); setPickerCursor(null); setPickerActiveIndex(0); return; }
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void handleGenerate(); }
  };

  return (
    <div
      ref={rootRef}
      className={`
        group relative flex h-full flex-col overflow-visible rounded-[var(--node-radius)] border bg-surface-dark/90 p-2 transition-colors duration-150
        ${selected
          ? 'border-accent shadow-[0_0_0_1px_rgba(59,130,246,0.32)]'
          : 'border-[rgba(15,23,42,0.22)] hover:border-[rgba(15,23,42,0.34)] dark:border-[rgba(255,255,255,0.22)] dark:hover:border-[rgba(255,255,255,0.34)]'}
      `}
      style={{ width: `${resolvedWidth}px`, height: `${resolvedHeight}px` }}
      onClick={() => setSelectedNode(id)}
    >
      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<Sparkles className="h-4 w-4" />}
        titleText={resolvedTitle}
        rightSlot={
          resolvedPriceDisplay ? <NodePriceBadge label={resolvedPriceDisplay.label} title={resolvedPriceTooltip} /> : undefined
        }
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <div className="relative min-h-0 flex-1 rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/45 p-2">
        <div className="relative h-full min-h-0">
          <div
            ref={promptHighlightRef}
            aria-hidden="true"
            className="ui-scrollbar pointer-events-none absolute inset-0 overflow-y-auto overflow-x-hidden text-sm leading-6 text-text-dark"
            style={{ scrollbarGutter: 'stable' }}
          >
            <div className="min-h-full whitespace-pre-wrap break-words px-1 py-0.5">
              {renderPromptWithHighlights(promptDraft, incomingImages.length)}
            </div>
          </div>
          <textarea
            ref={promptRef}
            value={promptDraft}
            onChange={(event) => { const v = event.target.value; setPromptDraft(v); commitPromptDraft(v); }}
            onKeyDown={handlePromptKeyDown}
            onScroll={syncPromptHighlightScroll}
            onMouseDown={(event) => event.stopPropagation()}
            placeholder={t('node.imageEdit.promptPlaceholder', 'Describe the image you want to generate...')}
            className="ui-scrollbar nodrag nowheel relative z-10 h-full w-full resize-none overflow-y-auto overflow-x-hidden border-none bg-transparent px-1 py-0.5 text-sm leading-6 text-transparent caret-text-dark outline-none placeholder:text-text-muted/80 focus:border-transparent whitespace-pre-wrap break-words"
            style={{ scrollbarGutter: 'stable' }}
          />
        </div>

        {showImagePicker && incomingImageItems.length > 0 && (
          <div
            className="nowheel absolute z-30 w-[120px] overflow-hidden rounded-xl border border-[rgba(255,255,255,0.16)] bg-surface-dark shadow-xl"
            style={{ left: pickerAnchor.left, top: pickerAnchor.top }}
            onMouseDown={(e) => e.stopPropagation()}
            onWheelCapture={(e) => e.stopPropagation()}
          >
            <div className="ui-scrollbar nowheel max-h-[180px] overflow-y-auto" onWheelCapture={(e) => e.stopPropagation()}>
              {incomingImageItems.map((item, index) => (
                <button key={`${item.imageUrl}-${index}`} type="button"
                  onClick={(e) => { e.stopPropagation(); insertImageReference(index); }}
                  onMouseEnter={() => setPickerActiveIndex(index)}
                  className={`flex w-full items-center gap-2 border border-transparent bg-bg-dark/70 px-2 py-2 text-left text-sm text-text-dark transition-colors hover:border-[rgba(255,255,255,0.18)] ${pickerActiveIndex === index ? 'border-[rgba(255,255,255,0.24)] bg-bg-dark' : ''}`}
                >
                  <CanvasNodeImage src={item.displayUrl} alt={item.label} viewerSourceUrl={resolveImageDisplayUrl(item.imageUrl)} viewerImageList={incomingImageViewerList} className="h-8 w-8 rounded object-cover" draggable={false} />
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Upstream image preview strip */}
      {incomingImageItems.length > 0 && (
        <div className="mt-1.5 shrink-0">
          <div className="mb-1 text-[9px] text-text-muted">Input Images ({incomingImageItems.length})</div>
          <div className="flex gap-1 overflow-x-auto">
            {incomingImageItems.slice(0, 6).map((item, index) => (
              <CanvasNodeImage
                key={`upstream-${index}`}
                src={item.displayUrl}
                alt={item.label}
                viewerSourceUrl={resolveImageDisplayUrl(item.imageUrl)}
                viewerImageList={incomingImageViewerList}
                className="h-10 w-10 shrink-0 rounded border border-[rgba(255,255,255,0.1)] object-cover"
                draggable={false}
              />
            ))}
            {incomingImageItems.length > 6 && (
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded border border-[rgba(255,255,255,0.1)] bg-bg-dark/50 text-[10px] text-text-muted">
                +{incomingImageItems.length - 6}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Negative prompt */}
      <div className="mt-1.5 shrink-0">
        <input
          type="text"
          value={(data.extraParams?.negative_prompt as string) ?? ''}
          onChange={(e) => {
            e.stopPropagation();
            updateNodeData(id, { extraParams: { ...(data.extraParams ?? {}), negative_prompt: e.target.value } });
          }}
          onMouseDown={(e) => e.stopPropagation()}
          placeholder={t('node.imageEdit.negativePrompt', 'Negative prompt (optional)...')}
          className="nodrag nowheel w-full rounded border border-[rgba(255,255,255,0.1)] bg-bg-dark/30 px-1.5 py-0.5 text-[11px] text-text-dark outline-none placeholder:text-text-muted/60"
        />
      </div>

      {/* Seed + controls row */}
      <div className="mt-1.5 flex shrink-0 items-center gap-1">
        <div className="flex items-center gap-1">
          <input
            type="number"
            value={(data.extraParams?.seed as number) ?? ''}
            onChange={(e) => {
              e.stopPropagation();
              const seedValue = e.target.value ? Number(e.target.value) : undefined;
              updateNodeData(id, { extraParams: { ...(data.extraParams ?? {}), seed: seedValue } });
            }}
            onMouseDown={(e) => e.stopPropagation()}
            placeholder="Seed"
            className="nodrag nowheel h-6 w-16 rounded border border-[rgba(255,255,255,0.1)] bg-bg-dark/30 px-1 text-[10px] text-text-dark outline-none placeholder:text-text-muted/60"
            title="Seed for reproducibility (leave empty for random)"
          />
        </div>
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
          extraParams={data.extraParams}
          onExtraParamChange={(key, value) => updateNodeData(id, { extraParams: { ...(data.extraParams ?? {}), [key]: value } })}
          showWebSearchToggle={showWebSearchToggle}
          webSearchEnabled={webSearchEnabled}
          onWebSearchToggle={(enabled) => updateNodeData(id, { extraParams: { ...(data.extraParams ?? {}), enable_web_search: enabled } })}
          triggerSize="sm"
          chipClassName={NODE_CONTROL_CHIP_CLASS}
          modelChipClassName={NODE_CONTROL_MODEL_CHIP_CLASS}
          paramsChipClassName={NODE_CONTROL_PARAMS_CHIP_CLASS}
        />
        <div className="ml-auto" />
        <UiButton
          onClick={(event) => { event.stopPropagation(); void handleGenerate(); }}
          variant="primary"
          className={`shrink-0 ${NODE_CONTROL_PRIMARY_BUTTON_CLASS}`}
        >
          <Sparkles className={NODE_CONTROL_ICON_CLASS} strokeWidth={2.8} />
          {t('canvas.generate', 'Generate')}
        </UiButton>
      </div>

      {error && (
        <div className="mt-1 flex shrink-0 items-center gap-2">
          <span className="text-xs text-red-400 flex-1">{error}</span>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setError(null); void handleGenerate(); }}
            className="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-red-300 border border-red-500/30 hover:bg-red-500/15 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      <Handle type="target" id="target" position={Position.Left} className="!h-2 !w-2 !border-surface-dark !bg-accent" />
      <Handle type="source" id="source" position={Position.Right} className="!h-2 !w-2 !border-surface-dark !bg-accent" />
      <NodeResizeHandle minWidth={IMAGE_EDIT_NODE_MIN_WIDTH} minHeight={IMAGE_EDIT_NODE_MIN_HEIGHT} maxWidth={IMAGE_EDIT_NODE_MAX_WIDTH} maxHeight={IMAGE_EDIT_NODE_MAX_HEIGHT} />
    </div>
  );
});

ImageEditNode.displayName = 'ImageEditNode';
