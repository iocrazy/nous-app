import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NodeToolbar as ReactFlowNodeToolbar } from '@xyflow/react';
import { Copy, Crop, Download, Info, PenLine, RefreshCw, Scissors, Trash2, Unlink2, FileDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  NODE_TOOL_TYPES,
  isExportImageNode,
  isGroupNode,
  isImageEditNode,
  isStoryboardGenNode,
  isStoryboardSplitNode,
  isUploadNode,
  type CanvasNode,
  type NodeToolType,
} from '../domain/canvasNodes';
import { canvasEventBus } from '../application/canvasServices';
import { getNodeToolPlugins } from '../tools';
import type { ToolIconKey } from '../tools';
import { UiChipButton, UiPanel } from '../../../components/ui';
import { useCanvasStore } from '../../../stores/canvasStore';
import {
  NODE_TOOLBAR_ALIGN,
  NODE_TOOLBAR_CLASS,
  NODE_TOOLBAR_OFFSET,
  NODE_TOOLBAR_POSITION,
} from './nodeToolbarConfig';

interface NodeActionToolbarProps {
  node: CanvasNode;
}

const toolIconMap: Record<ToolIconKey, typeof Crop> = {
  crop: Crop,
  annotate: PenLine,
  split: Scissors,
};

const TOOLBAR_BUTTON_RADIUS_CLASS = 'rounded-full';
const TOOLBAR_NEUTRAL_BUTTON_CLASS =
  'border-[rgba(255,255,255,0.18)] bg-bg-dark/70 text-text-dark hover:border-[rgba(255,255,255,0.32)] hover:bg-bg-dark';

export const NodeActionToolbar = memo(({ node }: NodeActionToolbarProps) => {
  const { t } = useTranslation();
  const isImageEdit = isImageEditNode(node);
  const isStoryboardGen = isStoryboardGenNode(node);
  const isStoryboardSplit = isStoryboardSplitNode(node);
  const canCopyStoryboardText = isStoryboardGen || isStoryboardSplit;
  const tools = useMemo(() => getNodeToolPlugins(node), [node]);
  const deleteNode = useCanvasStore((state) => state.deleteNode);
  const ungroupNode = useCanvasStore((state) => state.ungroupNode);
  const canReupload = isUploadNode(node) && Boolean(node.data.imageUrl);

  const [isCopySuccess, setIsCopySuccess] = useState(false);
  const [isCopyTextSuccess, setIsCopyTextSuccess] = useState(false);
  const [showInfo, setShowInfo] = useState(false);
  const copyFeedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copyTextFeedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const imageSource = useMemo(() => {
    if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
      return node.data.imageUrl || node.data.previewImageUrl || null;
    }
    return null;
  }, [node]);
  const canHandleImage = Boolean(imageSource);

  const generationError =
    isExportImageNode(node)
    && typeof (node.data as { generationError?: unknown }).generationError === 'string'
      ? ((node.data as { generationError?: string }).generationError ?? '').trim()
      : '';
  const canCopyGenerationError = isExportImageNode(node) && generationError.length > 0;

  useEffect(() => {
    return () => {
      if (copyFeedbackTimerRef.current) clearTimeout(copyFeedbackTimerRef.current);
      if (copyTextFeedbackTimerRef.current) clearTimeout(copyTextFeedbackTimerRef.current);
    };
  }, []);

  const resolveToolLabel = useCallback((toolType: NodeToolType) => {
    if (toolType === NODE_TOOL_TYPES.crop) return t('tool.crop', 'Crop');
    if (toolType === NODE_TOOL_TYPES.annotate) return t('tool.annotate', 'Annotate');
    if (toolType === NODE_TOOL_TYPES.splitStoryboard) return t('tool.split', 'Split');
    return '';
  }, [t]);

  const handleCopyImage = useCallback(async () => {
    if (!imageSource) return;
    setIsCopySuccess(true);
    if (copyFeedbackTimerRef.current) clearTimeout(copyFeedbackTimerRef.current);
    copyFeedbackTimerRef.current = setTimeout(() => { setIsCopySuccess(false); copyFeedbackTimerRef.current = null; }, 1100);
    try {
      const response = await fetch(imageSource);
      const blob = await response.blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    } catch (error) {
      console.error('Failed to copy image to clipboard', error);
    }
  }, [imageSource]);

  const storyboardText = useMemo(() => {
    if (isStoryboardGen) {
      return node.data.frames
        .map((frame, index) => `Frame ${String(index + 1).padStart(2, '0')}: ${frame.description ?? ''}`)
        .join('\n');
    }
    if (isStoryboardSplit) {
      const orderedFrames = [...node.data.frames].sort((a, b) => a.order - b.order);
      return orderedFrames
        .map((frame, index) => `Frame ${String(index + 1).padStart(2, '0')}: ${frame.note ?? ''}`)
        .join('\n');
    }
    return '';
  }, [isStoryboardGen, isStoryboardSplit, node]);

  const handleCopyStoryboardText = useCallback(async () => {
    if (!storyboardText) return;
    setIsCopyTextSuccess(true);
    if (copyTextFeedbackTimerRef.current) clearTimeout(copyTextFeedbackTimerRef.current);
    copyTextFeedbackTimerRef.current = setTimeout(() => { setIsCopyTextSuccess(false); copyTextFeedbackTimerRef.current = null; }, 1100);
    try { await navigator.clipboard.writeText(storyboardText); }
    catch (error) { console.error('Failed to copy storyboard text', error); }
  }, [storyboardText]);

  const handleCopyGenerationError = useCallback(async () => {
    if (!canCopyGenerationError) return;
    try { await navigator.clipboard.writeText(generationError); }
    catch (error) { console.error('Failed to copy generation error', error); }
  }, [canCopyGenerationError, generationError]);

  const handleDownload = useCallback(() => {
    if (!imageSource) return;
    const link = document.createElement('a');
    link.href = imageSource;
    link.download = `node-${node.id}.png`;
    link.click();
  }, [imageSource, node.id]);

  // Batch download helper (downloads the current image with auto naming)
  const handleDownloadWithName = useCallback(() => {
    if (!imageSource) return;
    const displayName = typeof (node.data as Record<string, unknown>).displayName === 'string'
      ? (node.data as Record<string, unknown>).displayName as string
      : '';
    const safeName = displayName.replace(/[^a-zA-Z0-9-_]/g, '_').slice(0, 40) || `node-${node.id.slice(0, 8)}`;
    const link = document.createElement('a');
    link.href = imageSource;
    link.download = `${safeName}.png`;
    link.click();
  }, [imageSource, node.data, node.id]);

  // Node metadata for info popover
  const nodeMetadata = useMemo(() => {
    const meta: Array<{ label: string; value: string }> = [];
    meta.push({ label: 'ID', value: node.id.slice(0, 12) });
    meta.push({ label: 'Type', value: node.type ?? 'unknown' });
    if (node.position) {
      meta.push({ label: 'Position', value: `${Math.round(node.position.x)}, ${Math.round(node.position.y)}` });
    }
    const nodeData = node.data as Record<string, unknown>;
    if (typeof nodeData.aspectRatio === 'string') {
      meta.push({ label: 'Aspect Ratio', value: nodeData.aspectRatio });
    }
    if (typeof nodeData.displayName === 'string' && nodeData.displayName) {
      meta.push({ label: 'Title', value: nodeData.displayName as string });
    }
    return meta;
  }, [node]);

  return (
    <ReactFlowNodeToolbar
      nodeId={node.id}
      isVisible
      position={NODE_TOOLBAR_POSITION}
      align={NODE_TOOLBAR_ALIGN}
      offset={NODE_TOOLBAR_OFFSET}
      className={NODE_TOOLBAR_CLASS}
    >
      <UiPanel className="flex items-center gap-1 rounded-full p-1">
        {/* Tool actions group */}
        {!isImageEdit && tools.length > 0 && (
          <>
            {tools.map((tool) => {
              const Icon = toolIconMap[tool.icon] ?? Crop;
              return (
                <UiChipButton
                  key={tool.type}
                  className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2.5 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS}`}
                  onClick={() =>
                    canvasEventBus.publish('tool-dialog/open', { nodeId: node.id, toolType: tool.type })
                  }
                  title={`${resolveToolLabel(tool.type)}`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {resolveToolLabel(tool.type)}
                </UiChipButton>
              );
            })}
            <div className="mx-0.5 h-5 w-px bg-[rgba(255,255,255,0.12)]" />
          </>
        )}

        {!isImageEdit && canReupload && (
          <UiChipButton
            key="upload-reupload"
            className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2.5 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS}`}
            onClick={() => canvasEventBus.publish('upload-node/reupload', { nodeId: node.id })}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t('nodeToolbar.reupload', 'Reupload')}
          </UiChipButton>
        )}

        {/* Copy/Download group */}
        {!isImageEdit && canHandleImage && (
          <>
            <UiChipButton
              key="image-copy"
              className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2.5 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS} ${
                isCopySuccess ? '!border-emerald-400/70 !bg-emerald-500/20 !text-emerald-200 hover:!bg-emerald-500/30' : ''
              }`}
              onClick={() => { void handleCopyImage(); }}
              title="Copy image to clipboard"
            >
              <Copy className="h-3.5 w-3.5" />
              {t('nodeToolbar.copy', 'Copy')}
            </UiChipButton>
            <UiChipButton
              key="image-download"
              className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2.5 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS}`}
              onClick={handleDownloadWithName}
              title="Download image with auto name"
            >
              <Download className="h-3.5 w-3.5" />
              {t('nodeToolbar.download', 'Download')}
            </UiChipButton>
          </>
        )}
        {!isImageEdit && canCopyStoryboardText && (
          <UiChipButton
            key="storyboard-text-copy"
            className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2.5 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS} ${
              isCopyTextSuccess ? '!border-emerald-400/70 !bg-emerald-500/20 !text-emerald-200 hover:!bg-emerald-500/30' : ''
            }`}
            onClick={() => { void handleCopyStoryboardText(); }}
            title="Copy frame text to clipboard"
          >
            <Copy className="h-3.5 w-3.5" />
            {t('nodeToolbar.copyText', 'Copy Text')}
          </UiChipButton>
        )}
        {!isImageEdit && canCopyGenerationError && (
          <UiChipButton
            key="generation-error-copy"
            className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2.5 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS} !border-red-500/45 !bg-red-500/15 !text-red-200 hover:!bg-red-500/25`}
            onClick={() => { void handleCopyGenerationError(); }}
            title="Copy error message"
          >
            <Copy className="h-3.5 w-3.5" />
            {t('nodeToolbar.copyErrorReport', 'Copy Error')}
          </UiChipButton>
        )}

        {/* Management group */}
        {(isGroupNode(node) || true) && (
          <div className="mx-0.5 h-5 w-px bg-[rgba(255,255,255,0.12)]" />
        )}

        {/* Info button */}
        <div className="relative">
          <UiChipButton
            key="node-info"
            className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS} ${
              showInfo ? '!border-indigo-400/60 !bg-indigo-500/20' : ''
            }`}
            onClick={(event) => { event.stopPropagation(); setShowInfo((v) => !v); }}
            title="Node info"
          >
            <Info className="h-3.5 w-3.5" />
          </UiChipButton>
          {showInfo && (
            <div
              className="absolute bottom-full left-1/2 z-50 mb-2 w-48 -translate-x-1/2 rounded-lg border border-[rgba(255,255,255,0.14)] bg-surface-dark p-2 shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              {nodeMetadata.map((item) => (
                <div key={item.label} className="flex items-center justify-between py-0.5 text-[10px]">
                  <span className="text-text-muted">{item.label}</span>
                  <span className="text-text-dark font-mono">{item.value}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {!isImageEdit && isGroupNode(node) && (
          <UiChipButton
            key="group-ungroup"
            className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} px-2.5 text-xs ${TOOLBAR_NEUTRAL_BUTTON_CLASS} hover:!border-amber-400/60 hover:!bg-amber-500/20 hover:!text-amber-200`}
            onClick={(event) => { event.stopPropagation(); ungroupNode(node.id); }}
            title="Ungroup nodes"
          >
            <Unlink2 className="h-3.5 w-3.5" />
            {t('nodeToolbar.ungroup', 'Ungroup')}
          </UiChipButton>
        )}
        <UiChipButton
          key="node-delete"
          className={`h-8 ${TOOLBAR_BUTTON_RADIUS_CLASS} border-red-500/45 bg-red-500/15 px-2.5 text-xs text-red-300 hover:bg-red-500/25`}
          onClick={(event) => { event.stopPropagation(); deleteNode(node.id); }}
          title="Delete node (Del)"
        >
          <Trash2 className="h-3.5 w-3.5" />
          {t('common.delete', 'Delete')}
        </UiChipButton>
      </UiPanel>
    </ReactFlowNodeToolbar>
  );
});

NodeActionToolbar.displayName = 'NodeActionToolbar';
